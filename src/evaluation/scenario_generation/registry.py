"""The single durable status center for the production pipeline.

All task state, artifact references, dependency invalidation and transition
events are recorded here in one ``registry.json``.  There is no second status
store anywhere in the production path.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from .models import (
    ArtifactRef,
    PipelineStage,
    RegistryEntry,
    RegistryEvent,
    ScenarioRegistry,
    _now,
    validate_transition,
)

# Artifact dependency direction: editing one of these invalidates the listed
# downstream artifacts.
_DOWNSTREAM: dict[str, list[str]] = {
    "task": ["kernel", "effect", "compiled", "path_validation", "runtime_check", "semantic_reviews", "human_decision"],
    "kernel": ["effect", "compiled", "path_validation", "runtime_check", "semantic_reviews", "human_decision"],
    "effect": ["compiled", "path_validation", "runtime_check", "semantic_reviews", "human_decision"],
    "compiled": ["path_validation", "runtime_check", "semantic_reviews", "human_decision"],
}

# The highest consistent stage given the artifacts that survived invalidation.
def stage_for_artifacts(artifacts: dict[str, ArtifactRef]) -> PipelineStage:
    has = artifacts.keys()
    if "compiled" in has and "effect" in has and "kernel" in has:
        return "COMPILED"
    if "effect" in has and "kernel" in has:
        return "EFFECT_READY"
    if "kernel" in has:
        return "KERNEL_READY"
    return "TASK_READY"


class PipelineRegistry:
    """Single durable status center with atomic writes and event history."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "registry.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> ScenarioRegistry:
        if not self.path.exists():
            return ScenarioRegistry()
        return ScenarioRegistry.model_validate_json(self.path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        self._data.updated_at = _now()
        fd, temp_name = tempfile.mkstemp(prefix=".registry.", dir=str(self.root))
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(self._data.model_dump_json(indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @property
    def data(self) -> ScenarioRegistry:
        return self._data

    def dump(self) -> dict[str, Any]:
        return self._data.model_dump(mode="json")

    # -- access --------------------------------------------------------------

    def get(self, task_id: str) -> RegistryEntry:
        try:
            return self._data.entries[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task_id: {task_id}") from exc

    def has(self, task_id: str) -> bool:
        return task_id in self._data.entries

    def entries(self) -> dict[str, RegistryEntry]:
        return self._data.entries

    # -- registration and transitions ---------------------------------------

    def register(self, task_id: str, case_id: str) -> RegistryEntry:
        existing = self._data.entries.get(task_id)
        if existing:
            if existing.case_id != case_id:
                raise ValueError(f"task {task_id} is already bound to another case")
            return existing
        entry = RegistryEntry(task_id=task_id, case_id=case_id, stage="TASK_READY")
        self._data.entries[task_id] = entry
        self._data.events.append(
            RegistryEvent(task_id=task_id, from_stage=None, to_stage="TASK_READY", generation=1, reason="task submitted")
        )
        self._save()
        return entry

    def transition(
        self,
        task_id: str,
        target: PipelineStage,
        *,
        reason: str,
        artifacts: dict[str, ArtifactRef] | None = None,
        generation_bump: bool = False,
    ) -> RegistryEntry:
        entry = self.get(task_id)
        validate_transition(entry.stage, target)
        previous = entry.stage
        if generation_bump:
            entry.generation += 1
            entry.invalidated_artifacts = []
            entry.errors = []
        if artifacts:
            entry.artifacts.update(artifacts)
        entry.stage = target
        entry.updated_at = _now()
        self._data.events.append(
            RegistryEvent(
                task_id=task_id,
                from_stage=previous,
                to_stage=target,
                generation=entry.generation,
                reason=reason,
            )
        )
        self._save()
        return entry

    def record_error(self, task_id: str, reason: str) -> RegistryEntry:
        entry = self.get(task_id)
        entry.errors.append(reason)
        entry.updated_at = _now()
        self._save()
        return entry

    def record_note(self, task_id: str, note: str) -> RegistryEntry:
        entry = self.get(task_id)
        entry.notes.append(note)
        entry.updated_at = _now()
        self._save()
        return entry

    def add_artifact(self, task_id: str, name: str, ref: ArtifactRef) -> RegistryEntry:
        entry = self.get(task_id)
        entry.artifacts[name] = ref
        entry.updated_at = _now()
        self._save()
        return entry

    # -- precise invalidation ------------------------------------------------

    def invalidate_artifact(
        self,
        task_id: str,
        from_artifact: str,
        *,
        reason: str,
    ) -> RegistryEntry:
        """Invalidate one artifact plus its dependents without wiping upstream.

        Upstream artifacts that already passed keep their entries; only the
        changed artifact and everything downstream of it are dropped.  The
        stage is recomputed from what remains.
        """

        entry = self.get(task_id)
        invalidated = [from_artifact, *_DOWNSTREAM.get(from_artifact, [])]
        dropped: list[str] = []
        for name in sorted(set(invalidated)):
            if name in entry.artifacts:
                entry.artifacts.pop(name, None)
                dropped.append(name)
        entry.invalidated_artifacts = sorted(
            set(entry.invalidated_artifacts) | set(dropped)
        )
        entry.errors.append(reason)
        recomputed = stage_for_artifacts(entry.artifacts)
        previous = entry.stage
        if recomputed != entry.stage:
            entry.generation += 1
            entry.stage = recomputed
            self._data.events.append(
                RegistryEvent(
                    task_id=task_id,
                    from_stage=previous,
                    to_stage=recomputed,
                    generation=entry.generation,
                    reason=reason,
                )
            )
        entry.updated_at = _now()
        self._save()
        return entry

    def invalidate_downstream(
        self,
        task_id: str,
        from_artifact: str,
        *,
        reason: str,
    ) -> RegistryEntry:
        """Drop every artifact downstream of one replaced artifact.

        Unlike :meth:`invalidate_artifact`, the replaced artifact itself is
        kept: this is used when a revision rewrites an artifact in place and
        only its dependents must be rebuilt.
        """

        entry = self.get(task_id)
        dependents = _DOWNSTREAM.get(from_artifact, [])
        dropped: list[str] = []
        for name in sorted(set(dependents)):
            if name in entry.artifacts:
                entry.artifacts.pop(name, None)
                dropped.append(name)
        entry.invalidated_artifacts = sorted(
            set(entry.invalidated_artifacts) | set(dropped)
        )
        entry.notes.append(reason)
        recomputed = stage_for_artifacts(entry.artifacts)
        previous = entry.stage
        if recomputed != entry.stage:
            entry.stage = recomputed
            self._data.events.append(
                RegistryEvent(
                    task_id=task_id,
                    from_stage=previous,
                    to_stage=recomputed,
                    generation=entry.generation,
                    reason=reason,
                )
            )
        entry.updated_at = _now()
        self._save()
        return entry


__all__ = ["PipelineRegistry", "stage_for_artifacts"]
