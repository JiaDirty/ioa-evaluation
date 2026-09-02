"""Production task-to-case orchestration for every scenario source.

This module is the single control plane for production work.  Historical
and generated inputs are normalized into :class:`ScenarioTask`; origin is
traceability metadata only and never selects a runtime branch.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..business_protocol.models import BusinessCaseSpec
from ..catalog import load_evaluation_catalog
from .pipeline import compile_kernel_effect, extract_effect_spec, extract_scenario_kernel
from .compiler import effect_from_case
from .path_validation import SixPathValidationReport
from .quality_records import HumanDecisionRecord, RuntimeCheckRecord, SemanticReviewRecord
from .pipeline_models import (
    EffectSpec,
    ScenarioKernel,
    seal_effect_spec,
    seal_kernel,
    verify_effect_kernel_binding,
    verify_effect_spec_hash,
    verify_kernel_hash,
)


SCENARIO_TASK_VERSION = "scenario_task_v1"
COMPILED_CASE_VERSION = "compiled_case_v1"
REGISTRY_VERSION = "scenario_registry_v1"
REGISTRY_EVENT_VERSION = "scenario_registry_event_v1"
ORCHESTRATOR_VERSION = "scenario_orchestrator_v1"

PipelineStage = Literal[
    "TASK_CREATED",
    "KERNEL_READY",
    "EFFECT_READY",
    "EFFECT_DRAFT",
    "EFFECT_NEEDS_REVISION",
    "COMPILED",
    "PATH_VALID",
    "RUNTIME_VALID",
    "SEMANTIC_ACCEPTED",
    "HUMAN_ACCEPTED",
    "FROZEN",
    "INVALIDATED",
    "CHECK_FAILED",
]
TaskOrigin = Literal["historical", "candidate", "generated", "manual"]

_STAGE_ORDER = {
    "TASK_CREATED": 0,
    "KERNEL_READY": 1,
    "EFFECT_READY": 2,
    "EFFECT_DRAFT": 2,
    "EFFECT_NEEDS_REVISION": 2,
    "COMPILED": 3,
    "PATH_VALID": 4,
    "RUNTIME_VALID": 5,
    "SEMANTIC_ACCEPTED": 6,
    "HUMAN_ACCEPTED": 7,
    "FROZEN": 8,
}
_ALLOWED_TRANSITIONS: dict[str | None, set[str]] = {
    None: {"TASK_CREATED"},
    "TASK_CREATED": {"KERNEL_READY", "CHECK_FAILED", "INVALIDATED"},
    "KERNEL_READY": {"EFFECT_DRAFT", "EFFECT_READY", "CHECK_FAILED", "INVALIDATED"},
    "EFFECT_DRAFT": {"EFFECT_READY", "EFFECT_NEEDS_REVISION", "CHECK_FAILED", "INVALIDATED"},
    "EFFECT_NEEDS_REVISION": {"EFFECT_DRAFT", "EFFECT_READY", "CHECK_FAILED", "INVALIDATED"},
    "EFFECT_READY": {"COMPILED", "CHECK_FAILED", "INVALIDATED"},
    "COMPILED": {"PATH_VALID", "CHECK_FAILED", "INVALIDATED"},
    "PATH_VALID": {"RUNTIME_VALID", "CHECK_FAILED", "INVALIDATED"},
    "CHECK_FAILED": {"INVALIDATED"},
    "RUNTIME_VALID": {"SEMANTIC_ACCEPTED", "INVALIDATED"},
    "SEMANTIC_ACCEPTED": {"HUMAN_ACCEPTED", "INVALIDATED"},
    "HUMAN_ACCEPTED": {"FROZEN", "INVALIDATED"},
    "FROZEN": {"INVALIDATED"},
    "INVALIDATED": {"TASK_CREATED"},
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_serialize(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_stable_serialize(value).encode("utf-8")).hexdigest()


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_case_dir(case_id: str, task_id: str) -> str:
    safe = "".join(char if char.isalnum() or char in "-_" else "-" for char in case_id)
    safe = safe.strip("-")[:90] or "case"
    return f"case_{safe}_{task_id.removeprefix('task-')[:12]}"


class TaskProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: TaskOrigin
    source_path: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_id: str | None = None
    seed: int | str | None = None
    prompt_version: str | None = None
    created_at: str = Field(default_factory=_now)


class ScenarioTask(BaseModel):
    """The only accepted input envelope for the production pipeline."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_task_v1"] = SCENARIO_TASK_VERSION
    task_id: str = Field(pattern=r"^task-[a-z0-9-]{12,100}$")
    case_id: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=2, max_length=100)
    title: str = Field(min_length=1, max_length=300)
    purpose: str = Field(min_length=1, max_length=2000)
    case_payload: dict[str, Any] = Field(min_length=1)
    provenance: TaskProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        catalog = load_evaluation_catalog()
        if value in catalog.category_names_zh:
            return catalog.code_for_name_zh(value)
        if value not in catalog.category_codes:
            raise ValueError(f"unknown evaluation category: {value}")
        return value

    @model_validator(mode="after")
    def validate_payload_binding(self) -> "ScenarioTask":
        case = BusinessCaseSpec.model_validate(self.case_payload)
        if case.case_id != self.case_id:
            raise ValueError("ScenarioTask case_id does not match case_payload")
        if case.category != self.category:
            raise ValueError("ScenarioTask category does not match case_payload")
        if case.title != self.title or case.purpose != self.purpose:
            raise ValueError("ScenarioTask summary fields do not match case_payload")
        return self

    @classmethod
    def from_case(
        cls,
        case: BusinessCaseSpec,
        *,
        task_id: str,
        provenance: TaskProvenance,
        metadata: dict[str, Any] | None = None,
    ) -> "ScenarioTask":
        task = cls(
            task_id=task_id,
            case_id=case.case_id,
            category=case.category,
            title=case.title,
            purpose=case.purpose,
            case_payload=case.model_dump(mode="json"),
            provenance=provenance,
            metadata=metadata or {},
        )
        return seal_task(task)


class CompiledCase(BaseModel):
    """Executable case plus immutable dependencies from the production chain."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["compiled_case_v1"] = COMPILED_CASE_VERSION
    task_id: str
    case_id: str
    kernel_id: str
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_id: str
    effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    case: BusinessCaseSpec
    compiled_at: str = Field(default_factory=_now)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relative_path(self) -> "ArtifactRef":
        normalized = self.path.replace("\\", "/")
        parsed = PurePosixPath(normalized)
        if (
            parsed.is_absolute()
            or normalized.startswith("/")
            or (len(normalized) >= 2 and normalized[1] == ":")
            or any(part == ".." for part in parsed.parts)
        ):
            raise ValueError("artifact path must be project-relative")
        return self


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    case_id: str
    stage: PipelineStage
    generation: int = Field(default=1, ge=1)
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    invalidated_artifacts: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


class RegistryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_registry_event_v1"] = REGISTRY_EVENT_VERSION
    task_id: str
    from_stage: PipelineStage | None
    to_stage: PipelineStage
    generation: int = Field(ge=1)
    reason: str = Field(min_length=1)
    at: str = Field(default_factory=_now)


class ScenarioRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_registry_v1"] = REGISTRY_VERSION
    orchestrator_version: Literal["scenario_orchestrator_v1"] = ORCHESTRATOR_VERSION
    entries: dict[str, RegistryEntry] = Field(default_factory=dict)
    events: list[RegistryEvent] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


def seal_task(task: ScenarioTask) -> ScenarioTask:
    return task.model_copy(update={"content_sha256": _digest(task.model_copy(update={"content_sha256": None}))})


def verify_task_hash(task: ScenarioTask) -> str:
    if not task.content_sha256:
        raise ValueError(f"task {task.task_id} is not sealed")
    actual = _digest(task.model_copy(update={"content_sha256": None}))
    if actual != task.content_sha256:
        raise ValueError(f"task {task.task_id} hash mismatch")
    return actual


def seal_compiled_case(case: CompiledCase) -> CompiledCase:
    return case.model_copy(update={"content_sha256": _digest(case.model_copy(update={"content_sha256": None}))})


def verify_compiled_case_hash(case: CompiledCase) -> str:
    if not case.content_sha256:
        raise ValueError(f"compiled case {case.case_id} is not sealed")
    actual = _digest(case.model_copy(update={"content_sha256": None}))
    if actual != case.content_sha256:
        raise ValueError(f"compiled case {case.case_id} hash mismatch")
    return actual


def validate_transition(current: PipelineStage | None, target: PipelineStage) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid pipeline transition: {current} -> {target}")


class PipelineRegistry:
    """Single durable status center for the production pipeline."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.path = self.root / "registry.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> ScenarioRegistry:
        if not self.path.exists():
            return ScenarioRegistry()
        return ScenarioRegistry.model_validate_json(self.path.read_text(encoding="utf-8"))

    @property
    def data(self) -> ScenarioRegistry:
        return self._data

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

    def register(self, task: ScenarioTask, case_dir: Path) -> RegistryEntry:
        verify_task_hash(task)
        existing = self._data.entries.get(task.task_id)
        if existing:
            if existing.case_id != task.case_id:
                raise ValueError(f"task {task.task_id} is already bound to another case")
            return existing
        entry = RegistryEntry(task_id=task.task_id, case_id=task.case_id, stage="TASK_CREATED")
        self._data.entries[task.task_id] = entry
        self._data.events.append(
            RegistryEvent(task_id=task.task_id, from_stage=None, to_stage="TASK_CREATED", generation=1, reason="task submitted")
        )
        self._save()
        return entry

    def get(self, task_id: str) -> RegistryEntry:
        try:
            return self._data.entries[task_id]
        except KeyError as exc:
            raise KeyError(f"unknown task_id: {task_id}") from exc

    def transition(
        self,
        task_id: str,
        target: PipelineStage,
        *,
        reason: str,
        artifacts: dict[str, ArtifactRef] | None = None,
    ) -> RegistryEntry:
        entry = self.get(task_id)
        validate_transition(entry.stage, target)
        previous = entry.stage
        if target == "TASK_CREATED" and previous == "INVALIDATED":
            entry.generation += 1
            entry.artifacts = {}
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

    def invalidate(self, task_id: str, *, from_artifact: str, reason: str) -> RegistryEntry:
        entry = self.get(task_id)
        if entry.stage == "INVALIDATED":
            return entry
        validate_transition(entry.stage, "INVALIDATED")
        downstream = {
            "task": [],
            "kernel": ["effect", "compiled"],
            "effect": ["compiled"],
            "compiled": [],
        }.get(from_artifact, [])
        invalidated = [from_artifact, *downstream]
        entry.invalidated_artifacts = sorted(set(entry.invalidated_artifacts + invalidated))
        for artifact in invalidated:
            entry.artifacts.pop(artifact, None)
        entry.errors.append(reason)
        previous = entry.stage
        entry.stage = "INVALIDATED"
        entry.updated_at = _now()
        self._data.events.append(
            RegistryEvent(
                task_id=task_id,
                from_stage=previous,
                to_stage="INVALIDATED",
                generation=entry.generation,
                reason=reason,
            )
        )
        self._save()
        return entry


class PipelineOrchestrator:
    """One public controller for submit, process and resume."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.cases_root = self.root / "cases"
        self.cases_root.mkdir(parents=True, exist_ok=True)
        self.registry = PipelineRegistry(self.root)

    def _case_dir(self, task: ScenarioTask) -> Path:
        return self.cases_root / _safe_case_dir(task.case_id, task.task_id)

    def _write_model(
        self,
        path: Path,
        model: BaseModel,
        *,
        depends_on: list[str] | None = None,
    ) -> ArtifactRef:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
        return ArtifactRef(
            path=path.relative_to(self.root).as_posix(),
            sha256=_file_digest(path),
            schema_version=str(model.model_dump(mode="json").get("schema_version", "unknown")),
            depends_on=list(depends_on or []),
        )

    def _write_lineage(self, task: ScenarioTask, entry: RegistryEntry) -> None:
        lineage_artifacts = {
            key: value.model_dump(mode="json")
            for key, value in entry.artifacts.items()
            if key != "lineage"
        }
        lineage = {
            "schema_version": "scenario_lineage_v1",
            "task_id": task.task_id,
            "generation": entry.generation,
            "stage": entry.stage,
            "artifacts": lineage_artifacts,
            "invalidated_artifacts": entry.invalidated_artifacts,
            "updated_at": _now(),
        }
        path = self._case_dir(task) / "lineage.json"
        path.write_text(json.dumps(lineage, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        entry.artifacts["lineage"] = ArtifactRef(
            path=path.relative_to(self.root).as_posix(),
            sha256=_file_digest(path),
            schema_version="scenario_lineage_v1",
            depends_on=sorted(key for key in entry.artifacts if key != "lineage"),
        )
        self.registry._save()

    def submit(self, task: ScenarioTask) -> RegistryEntry:
        task = seal_task(task)
        verify_task_hash(task)
        case_dir = self._case_dir(task)
        case_dir.mkdir(parents=True, exist_ok=True)
        task_path = case_dir / "scenario_task.json"
        if task_path.exists():
            existing = ScenarioTask.model_validate_json(task_path.read_text(encoding="utf-8"))
            if existing.content_sha256 != task.content_sha256:
                raise ValueError(f"task {task.task_id} already exists with different content")
        else:
            task_path.write_text(task.model_dump_json(indent=2) + "\n", encoding="utf-8")
        entry = self.registry.register(task, case_dir)
        if "task" not in entry.artifacts:
            entry.artifacts["task"] = ArtifactRef(
                path=task_path.relative_to(self.root).as_posix(),
                sha256=_file_digest(task_path),
                schema_version=SCENARIO_TASK_VERSION,
                depends_on=[],
            )
            self.registry._save()
        self._write_lineage(task, entry)
        return entry

    def submit_kernel(
        self,
        task_id: str,
        kernel: ScenarioKernel | dict[str, Any],
        *,
        reason: str = "ScenarioKernel supplied by generator or repair",
    ) -> RegistryEntry:
        """Persist a generated/repaired kernel through the same Registry."""

        task = self._load_task(task_id)
        entry = self.registry.get(task_id)
        if entry.stage != "TASK_CREATED":
            raise ValueError(f"kernel submission requires TASK_CREATED, got {entry.stage}")
        parsed = kernel if isinstance(kernel, ScenarioKernel) else ScenarioKernel.model_validate(kernel)
        if parsed.source.source_case_id not in {None, task.case_id}:
            raise ValueError("ScenarioKernel source_case_id does not match task")
        catalog = load_evaluation_catalog()
        allowed_categories = {task.category}
        if task.category in catalog.category_codes:
            allowed_categories.update(
                item.name_zh for item in catalog.categories if item.code == task.category
            )
        if parsed.category not in allowed_categories:
            raise ValueError("ScenarioKernel category does not match task")
        parsed = seal_kernel(parsed)
        case_dir = self._case_dir(task)
        ref = self._write_model(case_dir / "scenario_kernel.json", parsed, depends_on=["task"])
        entry = self.registry.transition(task_id, "KERNEL_READY", reason=reason, artifacts={"kernel": ref})
        self._write_lineage(task, entry)
        return entry

    def submit_effect(
        self,
        task_id: str,
        effect: EffectSpec | dict[str, Any],
        *,
        reason: str = "EffectSpec supplied by generator or repair",
    ) -> RegistryEntry:
        """Persist a generated/repaired, kernel-bound EffectSpec."""

        task = self._load_task(task_id)
        entry = self.registry.get(task_id)
        if entry.stage != "KERNEL_READY":
            raise ValueError(f"effect submission requires KERNEL_READY, got {entry.stage}")
        kernel_path = self.root / entry.artifacts["kernel"].path
        kernel = ScenarioKernel.model_validate_json(kernel_path.read_text(encoding="utf-8"))
        parsed = effect if isinstance(effect, EffectSpec) else EffectSpec.model_validate(effect)
        verify_effect_kernel_binding(kernel, parsed)
        if parsed.status != "READY_FOR_COMPILE":
            raise ValueError("submitted EffectSpec must be READY_FOR_COMPILE")
        parsed = seal_effect_spec(parsed)
        verify_effect_spec_hash(parsed)
        ref = self._write_model(self._case_dir(task) / "effect_spec.json", parsed, depends_on=["task", "kernel"])
        entry = self.registry.transition(task_id, "EFFECT_READY", reason=reason, artifacts={"effect": ref})
        self._write_lineage(task, entry)
        return entry

    def generate_kernel(
        self,
        task_id: str,
        *,
        prompt: str,
        config: Any,
        allow_live_api: bool = False,
        api: Any | None = None,
    ) -> RegistryEntry:
        """Call the opt-in generator, then commit its result to Registry."""

        from .pipeline_api import PipelineAPI

        task = self._load_task(task_id)
        if self.registry.get(task_id).stage != "TASK_CREATED":
            raise ValueError("kernel generation requires TASK_CREATED")
        provider = api or PipelineAPI()
        try:
            kernel = provider.generate_kernel(
                task_card=task.model_dump(mode="json"),
                prompt=prompt,
                candidate_uid=task_id,
                config=config,
                output_dir=self._case_dir(task) / "generation" / "kernel",
                source_case_id=task.case_id,
                allow_live_api=allow_live_api,
            )
        except Exception as exc:
            self.mark_generation_failed(task_id, stage="TASK_CREATED", reason=f"kernel generation failed: {exc}")
            raise
        return self.submit_kernel(task_id, kernel, reason="ScenarioKernel generated and validated")

    def generate_effect(
        self,
        task_id: str,
        *,
        prompt: str,
        config: Any,
        allow_live_api: bool = False,
        api: Any | None = None,
    ) -> RegistryEntry:
        """Call the opt-in effect generator, then commit its bound result."""

        from .pipeline_api import PipelineAPI

        task = self._load_task(task_id)
        entry = self.registry.get(task_id)
        if entry.stage != "KERNEL_READY":
            raise ValueError("effect generation requires KERNEL_READY")
        kernel = ScenarioKernel.model_validate_json(
            (self.root / entry.artifacts["kernel"].path).read_text(encoding="utf-8")
        )
        provider = api or PipelineAPI()
        try:
            effect = provider.generate_effect(
                kernel=kernel,
                prompt=prompt,
                config=config,
                output_dir=self._case_dir(task) / "generation" / "effect",
                allow_live_api=allow_live_api,
            )
        except Exception as exc:
            self.mark_generation_failed(task_id, stage="KERNEL_READY", reason=f"effect generation failed: {exc}")
            raise
        return self.submit_effect(task_id, effect, reason="EffectSpec generated, bound, and validated")

    def mark_generation_failed(self, task_id: str, *, stage: str, reason: str) -> RegistryEntry:
        """Stop a failed generation/revision at an explicit retryable state."""

        if stage not in {"TASK_CREATED", "KERNEL_READY", "EFFECT_READY"}:
            raise ValueError("generation failure stage must be TASK_CREATED, KERNEL_READY, or EFFECT_READY")
        entry = self.registry.get(task_id)
        if entry.stage != stage:
            raise ValueError(f"generation failure expected {stage}, got {entry.stage}")
        entry.errors.append(reason)
        self.registry._save()
        task = self._load_task(task_id)
        entry = self.registry.transition(task_id, "CHECK_FAILED", reason=reason)
        self._write_lineage(task, entry)
        return entry

    def _load_task(self, task_id: str) -> ScenarioTask:
        entry = self.registry.get(task_id)
        path = self.root / entry.artifacts["task"].path
        task = ScenarioTask.model_validate_json(path.read_text(encoding="utf-8"))
        verify_task_hash(task)
        return task

    @staticmethod
    def _record_for_task(task: ScenarioTask) -> Any:
        case = BusinessCaseSpec.model_validate(task.case_payload)
        provenance = task.provenance
        return SimpleNamespace(
            case=case,
            source_path=Path(provenance.source_path or task.task_id),
            generator_model_id=provenance.model_id or "pipeline-task",
            item_name=task.category,
            batch_id=task.task_id,
            candidate_uid=task.task_id,
        )

    def _verify_dependencies(self, task: ScenarioTask, entry: RegistryEntry) -> None:
        for name, ref in list(entry.artifacts.items()):
            path = self.root / ref.path
            if not path.exists() or _file_digest(path) != ref.sha256:
                artifact = (
                    "kernel" if name == "kernel" else
                    "effect" if name == "effect" else
                    "compiled" if name == "compiled" else
                    "lineage" if name == "lineage" else
                    "task"
                )
                self.registry.invalidate(task.task_id, from_artifact=artifact, reason=f"artifact hash mismatch: {name}")
                self._write_lineage(task, self.registry.get(task.task_id))
                raise ValueError(f"artifact hash mismatch: {name}")

    def process(self, task_id: str) -> RegistryEntry:
        task = self._load_task(task_id)
        entry = self.registry.get(task_id)
        self._verify_dependencies(task, entry)
        if entry.stage == "INVALIDATED":
            self.registry.transition(task_id, "TASK_CREATED", reason="resume new generation after invalidation")
            entry = self.registry.get(task_id)
        record = self._record_for_task(task)
        case = BusinessCaseSpec.model_validate(task.case_payload)
        case_dir = self._case_dir(task)
        if entry.stage == "TASK_CREATED":
            kernel = extract_scenario_kernel(record, source_sha256=task.provenance.source_sha256)
            kernel = seal_kernel(kernel)
            ref = self._write_model(case_dir / "scenario_kernel.json", kernel, depends_on=["task"])
            entry = self.registry.transition(task_id, "KERNEL_READY", reason="ScenarioKernel created", artifacts={"kernel": ref})
            self._write_lineage(task, entry)
        if entry.stage == "KERNEL_READY":
            kernel = ScenarioKernel.model_validate_json((case_dir / "scenario_kernel.json").read_text(encoding="utf-8"))
            if case.scoring_contract is not None:
                effect = effect_from_case(case, kernel)
            else:
                effect = extract_effect_spec(record, kernel)
                effect = seal_effect_spec(effect)
            ref = self._write_model(case_dir / "effect_spec.json", effect, depends_on=["task", "kernel"])
            effect_stage = "EFFECT_READY" if effect.status == "READY_FOR_COMPILE" else "EFFECT_DRAFT"
            entry = self.registry.transition(task_id, effect_stage, reason="EffectSpec created", artifacts={"effect": ref})
            self._write_lineage(task, entry)
        if entry.stage == "EFFECT_READY":
            kernel = ScenarioKernel.model_validate_json((case_dir / "scenario_kernel.json").read_text(encoding="utf-8"))
            effect = EffectSpec.model_validate_json((case_dir / "effect_spec.json").read_text(encoding="utf-8"))
            if effect.status != "READY_FOR_COMPILE":
                self._write_lineage(task, entry)
                return entry
            if case.scoring_contract is not None:
                compiled = case
            else:
                compiled = compile_kernel_effect(
                    kernel,
                    effect,
                    case_id=task.case_id,
                    category=task.category,
                    provenance={"task_id": task.task_id},
                )
            compiled_case = seal_compiled_case(
                CompiledCase(
                    task_id=task.task_id,
                    case_id=task.case_id,
                    kernel_id=kernel.kernel_id,
                    kernel_sha256=kernel.content_sha256 or "0" * 64,
                    effect_id=effect.effect_id,
                    effect_sha256=effect.content_sha256 or "0" * 64,
                    case=compiled,
                )
            )
            ref = self._write_model(
                case_dir / "compiled_case.json",
                compiled_case,
                depends_on=["task", "kernel", "effect"],
            )
            entry = self.registry.transition(task_id, "COMPILED", reason="CompiledCase created", artifacts={"compiled": ref})
            self._write_lineage(task, entry)
        if entry.stage in {"EFFECT_DRAFT", "EFFECT_NEEDS_REVISION"}:
            self._write_lineage(task, entry)
        return entry

    def resume(self, task_id: str) -> RegistryEntry:
        if self.registry.get(task_id).stage == "CHECK_FAILED":
            task = self._load_task(task_id)
            self.registry.invalidate(task_id, from_artifact="kernel", reason="resume after failed generation/check")
            self._write_lineage(task, self.registry.get(task_id))
        return self.process(task_id)

    def _record_quality_artifact(self, task_id: str, name: str, model: BaseModel) -> RegistryEntry:
        task = self._load_task(task_id)
        entry = self.registry.get(task_id)
        path = self._case_dir(task) / f"{name}.json"
        ref = self._write_model(path, model)
        entry.artifacts[name] = ref
        self.registry._save()
        return entry

    def record_path_validation(
        self,
        task_id: str,
        report: SixPathValidationReport | dict[str, Any],
    ) -> RegistryEntry:
        """Attach the six-path gate and advance only on PASS."""

        parsed = report if isinstance(report, SixPathValidationReport) else SixPathValidationReport.model_validate(report)
        entry = self.registry.get(task_id)
        if entry.stage != "COMPILED":
            raise ValueError(f"path validation requires COMPILED, got {entry.stage}")
        ref_entry = self._record_quality_artifact(task_id, "path_validation", parsed)
        if parsed.status != "PASS":
            result = self.registry.transition(task_id, "CHECK_FAILED", reason="six-path validation failed", artifacts={"path_validation": ref_entry.artifacts["path_validation"]})
        else:
            result = self.registry.transition(task_id, "PATH_VALID", reason="six-path validation passed", artifacts={"path_validation": ref_entry.artifacts["path_validation"]})
        self._write_lineage(self._load_task(task_id), result)
        return result

    def record_runtime_check(
        self,
        task_id: str,
        record: RuntimeCheckRecord | dict[str, Any],
    ) -> RegistryEntry:
        parsed = record if isinstance(record, RuntimeCheckRecord) else RuntimeCheckRecord.model_validate(record)
        entry = self.registry.get(task_id)
        if entry.stage != "PATH_VALID":
            raise ValueError(f"runtime check requires PATH_VALID, got {entry.stage}")
        ref_entry = self._record_quality_artifact(task_id, "runtime_check", parsed)
        target = "RUNTIME_VALID" if parsed.status == "PASS" else "CHECK_FAILED"
        result = self.registry.transition(task_id, target, reason=f"runtime check {parsed.status}", artifacts={"runtime_check": ref_entry.artifacts["runtime_check"]})
        self._write_lineage(self._load_task(task_id), result)
        return result

    def record_semantic_reviews(
        self,
        task_id: str,
        reviews: list[SemanticReviewRecord | dict[str, Any]],
    ) -> RegistryEntry:
        parsed = [item if isinstance(item, SemanticReviewRecord) else SemanticReviewRecord.model_validate(item) for item in reviews]
        if len(parsed) < 2:
            raise ValueError("semantic review requires at least two independent reviews")
        entry = self.registry.get(task_id)
        if entry.stage != "RUNTIME_VALID":
            raise ValueError(f"semantic review requires RUNTIME_VALID, got {entry.stage}")
        task = self._load_task(task_id)
        path = self._case_dir(task) / "semantic_reviews.json"
        path.write_text(json.dumps([item.model_dump(mode="json") for item in parsed], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        ref = ArtifactRef(path=path.relative_to(self.root).as_posix(), sha256=_file_digest(path), schema_version="semantic_review_bundle_v1")
        entry.artifacts["semantic_reviews"] = ref
        self.registry._save()
        accepted = all(item.decision == "ACCEPT" for item in parsed)
        target = "SEMANTIC_ACCEPTED" if accepted else "CHECK_FAILED"
        result = self.registry.transition(task_id, target, reason="all independent semantic reviews accepted" if accepted else "semantic review requires revision", artifacts={"semantic_reviews": ref})
        self._write_lineage(task, result)
        return result

    def record_human_decision(
        self,
        task_id: str,
        decision: HumanDecisionRecord | dict[str, Any],
    ) -> RegistryEntry:
        parsed = decision if isinstance(decision, HumanDecisionRecord) else HumanDecisionRecord.model_validate(decision)
        entry = self.registry.get(task_id)
        if entry.stage != "SEMANTIC_ACCEPTED":
            raise ValueError(f"human decision requires SEMANTIC_ACCEPTED, got {entry.stage}")
        ref_entry = self._record_quality_artifact(task_id, "human_decision", parsed)
        target = "HUMAN_ACCEPTED" if parsed.decision == "ACCEPT" else "CHECK_FAILED"
        result = self.registry.transition(task_id, target, reason=f"human decision {parsed.decision}", artifacts={"human_decision": ref_entry.artifacts["human_decision"]})
        self._write_lineage(self._load_task(task_id), result)
        return result

    def freeze(self, task_id: str) -> RegistryEntry:
        entry = self.registry.get(task_id)
        if entry.stage != "HUMAN_ACCEPTED":
            raise ValueError(f"freeze requires HUMAN_ACCEPTED, got {entry.stage}")
        task = self._load_task(task_id)
        result = self.registry.transition(task_id, "FROZEN", reason="human-accepted case frozen")
        self._write_lineage(task, result)
        return result


__all__ = [
    "ArtifactRef",
    "PipelineStage",
    "CompiledCase",
    "PipelineOrchestrator",
    "PipelineRegistry",
    "RegistryEntry",
    "RegistryEvent",
    "ScenarioRegistry",
    "ScenarioTask",
    "TaskOrigin",
    "TaskProvenance",
    "validate_transition",
    "seal_task",
    "verify_task_hash",
    "seal_compiled_case",
    "verify_compiled_case_hash",
]
