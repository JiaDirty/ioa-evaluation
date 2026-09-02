"""Compatibility registry builder for pre-canonical scenario artifacts.

The existing :mod:`pipeline_runner` is intentionally kept as the compatible
extract/repair/compile implementation.  This module adds the project-level
contracts described by the v1 production design:

* every candidate gets a program-generated ``TaskCard``;
* quality state is separate from release membership;
* registry paths are project-relative and never expose an absolute Windows
  path;
* status transitions and failures are recorded as deterministic events; and
* the legacy 440-candidate migration can be indexed without making a model
  call or changing a source file.

This module is retained only for historical evidence and regression tests.
New production code must use :class:`PipelineOrchestrator` from
``unified_architecture``.  It deliberately does not mark a draft
as accepted merely because its JSON parses.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..catalog import load_evaluation_catalog
from .pipeline_models import (
    PipelineManifest,
    PipelineManifestEntry,
    ScenarioKernel,
)


UNIFIED_PIPELINE_VERSION = "ioa_unified_pipeline_v1"
TASK_CARD_VERSION = "task_card_v1"
REGISTRY_VERSION = "candidate_registry_v1"
EVENT_VERSION = "candidate_registry_event_v1"
CANONICAL_CONDITIONS = ("baseline", "mechanism", "recovery")

UnifiedPipelineStage = Literal[
    "INGESTED",
    "KERNEL_DRAFT",
    "KERNEL_STRUCT_VALID",
    "KERNEL_SEMANTIC_VALID",
    "KERNEL_FROZEN",
    "EFFECT_DRAFT",
    "EFFECT_VALID",
    "COMPILED",
    "SIX_PATH_VALID",
    "RUNTIME_VALID",
    "DUAL_REVIEW_ACCEPTED",
    "HUMAN_ACCEPTED",
    "ACCEPTED",
    "NEEDS_REPAIR",
    "NEEDS_REWRITE",
    "QUARANTINED",
]

QualityStatus = Literal["PENDING", "ACCEPTED", "REJECTED"]
SourceKind = Literal["legacy", "generated", "manual"]
Difficulty = Literal["simple", "moderate", "complex"]

_STAGE_ORDER = {
    "INGESTED": 0,
    "KERNEL_DRAFT": 1,
    "KERNEL_STRUCT_VALID": 2,
    "KERNEL_SEMANTIC_VALID": 3,
    "KERNEL_FROZEN": 4,
    "EFFECT_DRAFT": 5,
    "EFFECT_VALID": 6,
    "COMPILED": 7,
    "SIX_PATH_VALID": 8,
    "RUNTIME_VALID": 9,
    "DUAL_REVIEW_ACCEPTED": 10,
    "HUMAN_ACCEPTED": 11,
    "ACCEPTED": 12,
}

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _slug(value: str) -> str:
    """Create a deterministic ASCII token for registry IDs."""

    normalized = value.strip().lower()
    normalized = _SLUG_RE.sub("-", normalized).strip("-")
    if normalized:
        return normalized[:80]
    return "item-" + _stable_hash(value)[:12]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _json_line(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            value.model_dump(mode="json") if isinstance(value, BaseModel) else value,
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, values: list[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(_json_line(value) for value in values)
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def _relative_project_path(path: str | Path, repo_root: Path) -> str:
    """Return a portable project-relative path and reject path traversal."""

    raw = Path(path).expanduser()
    resolved = raw.resolve()
    root = repo_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"artifact path is outside project root: {path}") from exc
    value = PurePosixPath(*relative.parts).as_posix()
    if value.startswith("../") or value == ".." or PurePosixPath(value).is_absolute():
        raise ValueError(f"artifact path is not project-relative: {path}")
    return value


class TaskCard(BaseModel):
    """Program-owned constraints for one candidate generation slot."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["task_card_v1"] = TASK_CARD_VERSION
    task_card_id: str = Field(pattern=r"^task-[a-z0-9-]{8,100}$")
    candidate_uid: str = Field(min_length=1)
    evaluation_item_id: str = Field(pattern=r"^[A-Z]{3}__[a-z0-9_-]+$")
    evaluation_item_name: str = Field(min_length=2, max_length=200)
    category_code: str = Field(pattern=r"^[A-Z]{3}$")
    category_name: str = Field(min_length=2, max_length=100)
    subtype: str | None = Field(default=None, max_length=100)
    mechanism: str = Field(min_length=8, max_length=1000)
    causal_variable_requirement: str = Field(min_length=8, max_length=2000)
    role_requirements: list[str] = Field(min_length=1, max_length=30)
    chain_requirements: list[str] = Field(min_length=1, max_length=30)
    observable_risk_states: list[str] = Field(min_length=1, max_length=200)
    recovery_requirements: list[str] = Field(min_length=1, max_length=30)
    business_domain: str = Field(min_length=1, max_length=300)
    difficulty: Difficulty = "moderate"
    novelty_constraints: list[str] = Field(default_factory=list, max_length=30)
    forbidden_combinations: list[str] = Field(default_factory=list, max_length=30)
    source_kind: SourceKind
    source_case_id: str | None = None
    prompt_version: str = Field(min_length=1, max_length=100)
    seed: int = Field(ge=0)
    created_at: str = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_catalog_binding(self) -> "TaskCard":
        catalog = load_evaluation_catalog()
        category = next(
            (item for item in catalog.categories if item.code == self.category_code),
            None,
        )
        if category is None:
            raise ValueError(f"unknown category code: {self.category_code}")
        if self.category_name != category.name_zh:
            raise ValueError("task card category_name does not match catalog")
        if self.evaluation_item_id.split("__", 1)[0] != self.category_code:
            raise ValueError("evaluation_item_id/category_code mismatch")
        for name, values in (
            ("role_requirements", self.role_requirements),
            ("chain_requirements", self.chain_requirements),
            ("observable_risk_states", self.observable_risk_states),
            ("recovery_requirements", self.recovery_requirements),
            ("novelty_constraints", self.novelty_constraints),
            ("forbidden_combinations", self.forbidden_combinations),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        return self


class FailureInfo(BaseModel):
    """A resumable, machine-readable failure record."""

    model_config = ConfigDict(extra="forbid")

    failed_stage: str = Field(min_length=1, max_length=100)
    reason_code: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=4000)
    repair_instruction: str = Field(min_length=1, max_length=4000)
    retry_count: int = Field(default=0, ge=0)
    resume_from: UnifiedPipelineStage


class CandidateRegistryEntry(BaseModel):
    """Stable index record for one candidate across all pipeline stages."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["candidate_registry_v1"] = REGISTRY_VERSION
    candidate_uid: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    evaluation_item_id: str = Field(pattern=r"^[A-Z]{3}__[a-z0-9_-]+$")
    evaluation_item_name: str = Field(min_length=2, max_length=200)
    category_code: str = Field(pattern=r"^[A-Z]{3}$")
    category_name: str = Field(min_length=2, max_length=100)
    subtype: str | None = None
    source_kind: SourceKind
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    task_card_path: str = Field(min_length=1)
    kernel_version: str | None = None
    kernel_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    effect_version: str | None = None
    effect_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    pipeline_stage: UnifiedPipelineStage
    quality_status: QualityStatus = "PENDING"
    release_membership: list[str] = Field(default_factory=list)
    failed_stage: str | None = None
    reason_code: str | None = None
    repair_instruction: str | None = None
    retry_count: int = Field(default=0, ge=0)
    revision_count: int = Field(default=0, ge=0)
    resume_from: UnifiedPipelineStage | None = None
    paths: dict[str, str] = Field(default_factory=dict)
    legacy_status: str | None = None
    created_at: str = Field(default_factory=_now)
    updated_at: str = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_paths_and_quality(self) -> "CandidateRegistryEntry":
        catalog = load_evaluation_catalog()
        category = next(
            (item for item in catalog.categories if item.code == self.category_code),
            None,
        )
        if category is None or self.category_name != category.name_zh:
            raise ValueError("registry category_name does not match catalog")
        for label, value in self.paths.items():
            normalized = value.replace("\\", "/")
            pure = PurePosixPath(normalized)
            if (
                pure.is_absolute()
                or ".." in pure.parts
                or re.match(r"^[A-Za-z]:/", normalized)
                or normalized.startswith("//")
            ):
                raise ValueError(f"registry path {label} must be project-relative")
        source_normalized = self.source_path.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", source_normalized) or source_normalized.startswith("/"):
            raise ValueError("registry source_path must be project-relative")
        if self.pipeline_stage == "ACCEPTED" and self.quality_status != "ACCEPTED":
            raise ValueError("ACCEPTED stage requires quality_status=ACCEPTED")
        if self.quality_status == "ACCEPTED" and self.pipeline_stage not in {
            "ACCEPTED",
            "HUMAN_ACCEPTED",
            "DUAL_REVIEW_ACCEPTED",
        }:
            raise ValueError("accepted quality requires completed review stage")
        if self.pipeline_stage in {"NEEDS_REPAIR", "NEEDS_REWRITE", "QUARANTINED"}:
            if not self.failed_stage or not self.reason_code or not self.resume_from:
                raise ValueError("exception stages require failure and resume metadata")
        return self


class RegistryEvent(BaseModel):
    """Append-only status transition evidence."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["candidate_registry_event_v1"] = EVENT_VERSION
    event_id: str = Field(pattern=r"^evt-[a-z0-9]{16,64}$")
    candidate_uid: str = Field(min_length=1)
    from_stage: UnifiedPipelineStage | None = None
    to_stage: UnifiedPipelineStage
    reason_code: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=4000)
    artifact_refs: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


class RegistryManifest(BaseModel):
    """Summary and integrity metadata for one registry build."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["candidate_registry_v1"] = REGISTRY_VERSION
    pipeline_version: str = UNIFIED_PIPELINE_VERSION
    source_manifest_path: str
    source_manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    registry_root: str
    path_policy: Literal["project_relative"] = "project_relative"
    candidate_count: int = Field(ge=0)
    all_candidates_processed: bool
    stage_counts: dict[str, int] = Field(default_factory=dict)
    quality_status_counts: dict[str, int] = Field(default_factory=dict)
    evaluation_item_counts: dict[str, int] = Field(default_factory=dict)
    source_hash_match_count: int = Field(ge=0)
    live_api_calls: int = Field(default=0, ge=0)
    generated_at: str = Field(default_factory=_now)


class RegistryBuildResult(BaseModel):
    """Small return value used by the CLI and tests."""

    model_config = ConfigDict(extra="forbid")

    registry_dir: str
    manifest_path: str
    candidate_count: int
    event_count: int
    task_card_count: int
    all_candidates_processed: bool
    stage_counts: dict[str, int]
    quality_status_counts: dict[str, int]
    live_api_calls: int = 0


def _read_jsonl_models(path: Path, model_type: type[BaseModel]) -> list[BaseModel]:
    values: list[BaseModel] = []
    if not path.is_file():
        raise FileNotFoundError(path)
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            values.append(model_type.model_validate(json.loads(line)))
        except Exception as exc:
            raise ValueError(f"invalid registry JSONL {path} line {line_number}: {exc}") from exc
    return values


def validate_unified_registry(
    registry_dir: str | Path,
    *,
    repo_root: str | Path | None = None,
    verify_source_hashes: bool = True,
) -> dict[str, Any]:
    """Validate registry files and return a machine-readable report.

    Validation is intentionally independent of the builder so a copied or
    archived registry can be checked without rebuilding it.
    """

    directory = Path(registry_dir).expanduser().resolve()
    root = Path(repo_root).expanduser().resolve() if repo_root else Path(__file__).resolve().parents[3]
    manifest_path = directory / "registry_manifest.json"
    manifest = RegistryManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    entries = [
        item
        for item in _read_jsonl_models(directory / "candidates.jsonl", CandidateRegistryEntry)
    ]
    cards = [item for item in _read_jsonl_models(directory / "task_cards.jsonl", TaskCard)]
    events = [item for item in _read_jsonl_models(directory / "events.jsonl", RegistryEvent)]
    entry_uids = {item.candidate_uid for item in entries}
    card_uids = {item.candidate_uid for item in cards}
    if len(entries) != manifest.candidate_count:
        raise ValueError("registry candidate count does not match registry manifest")
    if len(entry_uids) != len(entries):
        raise ValueError("registry contains duplicate candidate_uid values")
    if entry_uids != card_uids:
        raise ValueError("candidate registry and task cards do not cover the same UIDs")
    missing_paths: list[str] = []
    absolute_paths: list[str] = []
    hash_mismatches: list[str] = []
    for entry in entries:
        for label, value in entry.paths.items():
            normalized = value.replace("\\", "/")
            if (
                PurePosixPath(normalized).is_absolute()
                or ".." in PurePosixPath(normalized).parts
                or re.match(r"^[A-Za-z]:/", normalized)
                or normalized.startswith("//")
            ):
                absolute_paths.append(f"{entry.candidate_uid}:{label}")
            if not normalized.startswith("external/"):
                candidate_path = root / Path(*PurePosixPath(normalized).parts)
                if not candidate_path.is_file():
                    missing_paths.append(normalized)
        source = root / Path(*PurePosixPath(entry.source_path).parts)
        if verify_source_hashes and source.is_file():
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            if digest != entry.source_sha256:
                hash_mismatches.append(entry.candidate_uid)
    if absolute_paths:
        raise ValueError(f"registry contains absolute or traversing paths: {absolute_paths[:5]}")
    if missing_paths:
        raise ValueError(f"registry references missing artifacts: {missing_paths[:5]}")
    if hash_mismatches:
        raise ValueError(f"registry source hash mismatch: {hash_mismatches[:5]}")
    stage_counts = dict(sorted(Counter(item.pipeline_stage for item in entries).items()))
    quality_counts = dict(sorted(Counter(item.quality_status for item in entries).items()))
    if stage_counts != manifest.stage_counts or quality_counts != manifest.quality_status_counts:
        raise ValueError("registry status counts do not match registry manifest")
    report = {
        "schema_version": "unified_registry_validation_v1",
        "registry_dir": _relative_project_path(directory, root),
        "candidate_count": len(entries),
        "task_card_count": len(cards),
        "event_count": len(events),
        "all_candidates_processed": manifest.all_candidates_processed,
        "source_hashes_verified": bool(verify_source_hashes),
        "source_hash_mismatch_count": len(hash_mismatches),
        "absolute_path_count": len(absolute_paths),
        "missing_artifact_count": len(missing_paths),
        "status": "VALID",
        "validated_at": _now(),
    }
    _write_json(directory / "registry_validation.json", report)
    return report


def is_valid_transition(
    from_stage: UnifiedPipelineStage | None,
    to_stage: UnifiedPipelineStage,
) -> bool:
    """Return whether a registry status transition is allowed."""

    if from_stage is None:
        return to_stage == "INGESTED"
    if from_stage == to_stage:
        return True
    linear = (
        "INGESTED",
        "KERNEL_DRAFT",
        "KERNEL_STRUCT_VALID",
        "KERNEL_SEMANTIC_VALID",
        "KERNEL_FROZEN",
        "EFFECT_DRAFT",
        "EFFECT_VALID",
        "COMPILED",
        "SIX_PATH_VALID",
        "RUNTIME_VALID",
        "DUAL_REVIEW_ACCEPTED",
        "HUMAN_ACCEPTED",
        "ACCEPTED",
    )
    if from_stage in linear and to_stage in linear and linear.index(to_stage) > linear.index(from_stage):
        return True
    return to_stage in {"NEEDS_REPAIR", "NEEDS_REWRITE", "QUARANTINED"}


def validate_transition(
    from_stage: UnifiedPipelineStage | None,
    to_stage: UnifiedPipelineStage,
) -> None:
    if not is_valid_transition(from_stage, to_stage):
        raise ValueError(f"invalid pipeline transition: {from_stage} -> {to_stage}")


def _catalog_item(category_code: str, item_name: str | None) -> tuple[Any, str | None, str, str]:
    catalog = load_evaluation_catalog()
    category = next((item for item in catalog.categories if item.code == category_code), None)
    if category is None:
        raise ValueError(f"unknown category code: {category_code}")
    display = item_name or category.name_zh
    parts = display.split("__", 1)
    subtype = None
    if len(parts) == 2 and parts[1] and parts[1] != "default":
        subtype = parts[1]
    item_id = f"{category.code}__{_slug(subtype or 'default')}"
    item_label = category.name_zh if subtype is None else f"{category.name_zh}（{subtype}）"
    return category, subtype, item_id, item_label


def build_task_card(
    entry: PipelineManifestEntry,
    kernel: ScenarioKernel,
    *,
    duplicate_evidence: list[dict[str, Any]] | None = None,
    source_kind: SourceKind = "legacy",
) -> TaskCard:
    """Derive a program-owned task card without asking a model to choose a class."""

    category, subtype, item_id, item_label = _catalog_item(
        entry.category, entry.evaluation_item
    )
    role_requirements = [
        f"{role.role_id}: {role.responsibility}" for role in kernel.roles
    ]
    chain = kernel.metadata.get("chain_structure")
    chain_requirements = [
        f"业务链路：{chain}" if isinstance(chain, str) and chain.strip() else "按主步骤的上游依赖顺序传递产物",
        "恢复步骤只能处置风险路径已经产生的事实，不得预写未来结果",
    ]
    risk_states = _unique(
        [
            path
            for step in [*kernel.steps, *kernel.recovery_steps]
            for path in step.visible_state_paths
        ]
        + [
            key
            for condition in ("risk", "recovery")
            for key in kernel.initial_state.get(condition, {})
        ]
        + [
            item
            for step in [*kernel.steps, *kernel.recovery_steps]
            for item in step.observable_risk_impacts
        ]
    )
    if not risk_states:
        risk_states = ["EffectSpec 必须声明至少一个可观察业务状态字段"]
    recovery_requirements = [kernel.recovery_goal]
    recovery_ids = [
        step.step_id
        for step in kernel.recovery_steps
        if step.recovery_source_step_ids
    ]
    if recovery_ids:
        recovery_requirements.append(
            "恢复步骤必须绑定风险来源：" + ", ".join(recovery_ids)
        )
    else:
        recovery_requirements.append("恢复来源尚未绑定，必须在语义修复阶段确认")
    duplicate_evidence = duplicate_evidence or []
    novelty_constraints = [
        "不得复用已有候选的核心业务事实、角色组合和因果变量表达"
    ]
    if duplicate_evidence:
        novelty_constraints.append("本候选已有重复/高相似证据，修复时必须重新设计差异")
    difficulty: Difficulty = "simple"
    complexity = len(kernel.steps) + len(kernel.recovery_steps) + len(kernel.roles)
    if complexity >= 8:
        difficulty = "complex"
    elif complexity >= 5:
        difficulty = "moderate"
    digest = _stable_hash(entry.candidate_uid)
    return TaskCard(
        task_card_id=f"task-{digest[:24]}",
        candidate_uid=entry.candidate_uid,
        evaluation_item_id=item_id,
        evaluation_item_name=item_label,
        category_code=category.code,
        category_name=category.name_zh,
        subtype=subtype,
        mechanism=category.mechanism,
        causal_variable_requirement=kernel.causal_variable,
        role_requirements=role_requirements,
        chain_requirements=_unique(chain_requirements),
        observable_risk_states=risk_states,
        recovery_requirements=_unique(recovery_requirements),
        business_domain=kernel.business_domain,
        difficulty=difficulty,
        novelty_constraints=_unique(novelty_constraints),
        forbidden_combinations=[],
        source_kind=source_kind,
        source_case_id=entry.source_case_id,
        prompt_version=TASK_CARD_VERSION,
        seed=int(digest[:12], 16),
        created_at=entry.updated_at,
    )


def _stage_for_entry(entry: PipelineManifestEntry) -> UnifiedPipelineStage:
    if entry.status == "QUARANTINED":
        return "QUARANTINED"
    if entry.status == "REWRITE_REQUIRED":
        return "NEEDS_REWRITE"
    if entry.status in {"REVISE_REQUIRED", "REPAIR_PENDING"}:
        return "NEEDS_REPAIR"
    if entry.status == "REPAIR_VALID":
        return "EFFECT_VALID"
    if entry.status in {
        "SIX_PATH_VALID",
        "COMPILED",
        "RUNTIME_VALID",
        "SEMANTIC_ACCEPTED",
        "HUMAN_ACCEPTED",
        "FORMAL_ACCEPTED",
    }:
        return {
            "SIX_PATH_VALID": "SIX_PATH_VALID",
            "COMPILED": "COMPILED",
            "RUNTIME_VALID": "RUNTIME_VALID",
            "SEMANTIC_ACCEPTED": "DUAL_REVIEW_ACCEPTED",
            "HUMAN_ACCEPTED": "HUMAN_ACCEPTED",
            "FORMAL_ACCEPTED": "ACCEPTED",
        }[entry.status]
    if entry.effect_status == "READY_FOR_COMPILE":
        return "EFFECT_VALID"
    if entry.effect_id:
        return "EFFECT_DRAFT"
    if entry.kernel_id:
        return "KERNEL_STRUCT_VALID"
    if entry.source_path:
        return "INGESTED"
    return "INGESTED"


def _resume_from(stage: UnifiedPipelineStage) -> UnifiedPipelineStage:
    if stage == "NEEDS_REWRITE":
        return "KERNEL_DRAFT"
    if stage == "NEEDS_REPAIR":
        return "EFFECT_DRAFT"
    if stage == "QUARANTINED":
        return "INGESTED"
    return stage


def _failure_for_entry(
    entry: PipelineManifestEntry,
    stage: UnifiedPipelineStage,
) -> FailureInfo | None:
    if stage not in {"NEEDS_REPAIR", "NEEDS_REWRITE", "QUARANTINED"}:
        return None
    latest = entry.errors[-1] if entry.errors else None
    if latest is None:
        failed_stage = "repair" if stage == "NEEDS_REPAIR" else "kernel"
        code = "PENDING_REVIEW" if stage != "QUARANTINED" else "QUARANTINED"
        reason = entry.terminal_reason or "等待后续处理"
    else:
        failed_stage = latest.stage
        code = latest.code
        reason = latest.message
    instruction = {
        "NEEDS_REPAIR": "根据原始内核和修复任务补齐 EffectSpec；不得修改已冻结业务事实。",
        "NEEDS_REWRITE": "保留原始候选作为来源，重新设计无法成立的场景内核并建立新版本。",
        "QUARANTINED": "人工确认是否存在可继承的业务事实；不能安全推导时保持隔离。",
    }[stage]
    return FailureInfo(
        failed_stage=failed_stage,
        reason_code=code,
        reason=reason,
        repair_instruction=instruction,
        retry_count=sum(entry.attempts.values()) + entry.repair_attempts,
        resume_from=_resume_from(stage),
    )


def _path_map(entry: PipelineManifestEntry, repo_root: Path, task_card_path: Path) -> dict[str, str]:
    paths: dict[str, str] = {}
    for name, value in entry.stage_paths.items():
        if not value:
            continue
        try:
            paths[name] = _relative_project_path(value, repo_root)
        except ValueError:
            # A registry must never leak an absolute path.  External artifacts
            # are represented by a stable opaque location and remain auditable
            # through the source hash in the entry.
            digest = _stable_hash(str(value))[:16]
            paths[name] = f"external/{digest}/{Path(value).name}"
    paths["task_card"] = _relative_project_path(task_card_path, repo_root)
    return paths


def _entry_and_events(
    entry: PipelineManifestEntry,
    kernel: ScenarioKernel,
    task_card_path: Path,
    *,
    repo_root: Path,
) -> tuple[CandidateRegistryEntry, list[RegistryEvent]]:
    category, subtype, item_id, item_label = _catalog_item(
        entry.category, entry.evaluation_item
    )
    stage = _stage_for_entry(entry)
    failure = _failure_for_entry(entry, stage)
    kernel_version = kernel.schema_version if kernel else None
    effect_version = None
    effect_path = entry.stage_paths.get("effect_spec")
    if effect_path and Path(effect_path).is_file():
        try:
            effect_version = json.loads(Path(effect_path).read_text(encoding="utf-8")).get(
                "schema_version"
            )
        except (OSError, json.JSONDecodeError):
            effect_version = None
    quality: QualityStatus = "PENDING"
    if stage == "ACCEPTED" or stage in {"HUMAN_ACCEPTED", "DUAL_REVIEW_ACCEPTED"}:
        quality = "ACCEPTED"
    elif stage == "QUARANTINED":
        quality = "REJECTED"
    registry_entry = CandidateRegistryEntry(
        candidate_uid=entry.candidate_uid,
        case_id=entry.source_case_id,
        evaluation_item_id=item_id,
        evaluation_item_name=item_label,
        category_code=category.code,
        category_name=category.name_zh,
        subtype=subtype,
        source_kind=entry.source_kind,
        source_path=_relative_project_path(entry.source_path, repo_root),
        source_sha256=entry.source_sha256,
        task_card_path=_relative_project_path(task_card_path, repo_root),
        kernel_version=kernel_version,
        kernel_sha256=entry.kernel_sha256,
        effect_version=effect_version,
        effect_sha256=entry.effect_sha256,
        pipeline_stage=stage,
        quality_status=quality,
        release_membership=[],
        failed_stage=failure.failed_stage if failure else None,
        reason_code=failure.reason_code if failure else None,
        repair_instruction=failure.repair_instruction if failure else None,
        retry_count=failure.retry_count if failure else sum(entry.attempts.values()),
        revision_count=entry.repair_attempts,
        resume_from=failure.resume_from if failure else None,
        paths=_path_map(entry, repo_root, task_card_path),
        legacy_status=entry.status,
        created_at=entry.updated_at,
        updated_at=entry.updated_at,
    )
    events: list[RegistryEvent] = []
    current: UnifiedPipelineStage | None = None
    transitions: list[tuple[UnifiedPipelineStage, str, str]] = []
    if entry.source_path:
        transitions.append(("INGESTED", "INGEST", "候选已登记且原始文件保留"))
    if entry.kernel_id:
        transitions.extend(
            [
                ("KERNEL_DRAFT", "KERNEL_EXTRACTED", "已保存 ScenarioKernel 中间稿"),
                ("KERNEL_STRUCT_VALID", "KERNEL_STRUCT_VALID", "ScenarioKernel 通过本地结构校验"),
            ]
        )
    if entry.effect_id:
        transitions.append(("EFFECT_DRAFT", "EFFECT_DRAFT", "已保存 EffectSpec 草案；尚未视为可运行"))
    if stage not in {"INGESTED", "KERNEL_DRAFT", "KERNEL_STRUCT_VALID", "EFFECT_DRAFT"}:
        if stage in {"NEEDS_REPAIR", "NEEDS_REWRITE", "QUARANTINED"}:
            transitions.append((stage, failure.reason_code if failure else stage, failure.reason if failure else "需要后续处理"))
        else:
            transitions.append((stage, stage, "已达到该流水线阶段"))
    for index, (to_stage, code, reason) in enumerate(transitions):
        event_digest = _stable_hash(f"{entry.candidate_uid}:{index}:{to_stage}:{code}")
        events.append(
            RegistryEvent(
                event_id=f"evt-{event_digest[:24]}",
                candidate_uid=entry.candidate_uid,
                from_stage=current,
                to_stage=to_stage,
                reason_code=code,
                reason=reason,
                artifact_refs=list(registry_entry.paths.values()),
                created_at=entry.updated_at,
            )
        )
        current = to_stage
    previous: UnifiedPipelineStage | None = None
    for event in events:
        validate_transition(previous, event.to_stage)
        previous = event.to_stage
    return registry_entry, events


def build_unified_registry(
    pipeline_output: str | Path,
    *,
    repo_root: str | Path | None = None,
) -> RegistryBuildResult:
    """Build task cards, registry, events and migration statistics offline."""

    output = Path(pipeline_output).expanduser().resolve()
    manifest_path = output / "pipeline_manifest.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(f"pipeline manifest not found: {manifest_path}")
    manifest = PipelineManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
    root = Path(repo_root).expanduser().resolve() if repo_root else Path(__file__).resolve().parents[3]
    registry_dir = output / "registry"
    task_dir = registry_dir / "task_cards"
    task_dir.mkdir(parents=True, exist_ok=True)
    registry_entries: list[CandidateRegistryEntry] = []
    events: list[RegistryEvent] = []
    cards: list[TaskCard] = []
    for entry in sorted(manifest.entries, key=lambda item: item.candidate_uid):
        kernel_path_value = entry.stage_paths.get("kernel")
        if not kernel_path_value or not Path(kernel_path_value).is_file():
            raise ValueError(f"candidate {entry.candidate_uid} has no kernel artifact")
        kernel = ScenarioKernel.model_validate_json(
            Path(kernel_path_value).read_text(encoding="utf-8")
        )
        review_payload: dict[str, Any] = {}
        review_path = entry.stage_paths.get("kernel_review")
        if review_path and Path(review_path).is_file():
            try:
                review_payload = json.loads(Path(review_path).read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                review_payload = {}
        duplicate_evidence = review_payload.get("duplicate_evidence", [])
        if not isinstance(duplicate_evidence, list):
            duplicate_evidence = []
        card = build_task_card(
            entry,
            kernel,
            duplicate_evidence=duplicate_evidence,
            source_kind=entry.source_kind,
        )
        card_path = task_dir / f"{_stable_hash(entry.candidate_uid)[:24]}.json"
        _write_json(card_path, card)
        cards.append(card)
        registry_entry, entry_events = _entry_and_events(
            entry, kernel, card_path, repo_root=root
        )
        registry_entries.append(registry_entry)
        events.extend(entry_events)

    candidates_path = registry_dir / "candidates.jsonl"
    events_path = registry_dir / "events.jsonl"
    cards_path = registry_dir / "task_cards.jsonl"
    _write_jsonl(candidates_path, registry_entries)
    _write_jsonl(events_path, events)
    _write_jsonl(cards_path, cards)
    source_manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    stage_counts = dict(sorted(Counter(item.pipeline_stage for item in registry_entries).items()))
    quality_counts = dict(sorted(Counter(item.quality_status for item in registry_entries).items()))
    item_counts = dict(sorted(Counter(item.evaluation_item_name for item in registry_entries).items()))
    registry_manifest = RegistryManifest(
        source_manifest_path=_relative_project_path(manifest_path, root),
        source_manifest_sha256=source_manifest_sha,
        registry_root=_relative_project_path(registry_dir, root),
        candidate_count=len(registry_entries),
        all_candidates_processed=len(registry_entries) == len(manifest.entries),
        stage_counts=stage_counts,
        quality_status_counts=quality_counts,
        evaluation_item_counts=item_counts,
        source_hash_match_count=sum(item.source_hash_verified for item in manifest.entries),
        live_api_calls=0,
    )
    manifest_json_path = registry_dir / "registry_manifest.json"
    _write_json(manifest_json_path, registry_manifest)
    report = {
        "schema_version": "unified_migration_report_v1",
        "pipeline_version": UNIFIED_PIPELINE_VERSION,
        "candidate_count": len(registry_entries),
        "all_candidates_processed": registry_manifest.all_candidates_processed,
        "source_hash_match_count": registry_manifest.source_hash_match_count,
        "stage_counts": stage_counts,
        "quality_status_counts": quality_counts,
        "evaluation_item_counts": item_counts,
        "live_api_calls": 0,
        "semantic_repairs_performed": 0,
        "note": "本报告只记录离线登记和迁移；没有调用模型，草案不会自动变成正式数据。",
        "generated_at": _now(),
    }
    _write_json(registry_dir / "migration_report.json", report)
    return RegistryBuildResult(
        registry_dir=str(registry_dir),
        manifest_path=str(manifest_json_path),
        candidate_count=len(registry_entries),
        event_count=len(events),
        task_card_count=len(cards),
        all_candidates_processed=registry_manifest.all_candidates_processed,
        stage_counts=stage_counts,
        quality_status_counts=quality_counts,
        live_api_calls=0,
    )


__all__ = [
    "CANONICAL_CONDITIONS",
    "CandidateRegistryEntry",
    "Difficulty",
    "FailureInfo",
    "QualityStatus",
    "REGISTRY_VERSION",
    "RegistryBuildResult",
    "RegistryEvent",
    "RegistryManifest",
    "SourceKind",
    "TASK_CARD_VERSION",
    "TaskCard",
    "UNIFIED_PIPELINE_VERSION",
    "UnifiedPipelineStage",
    "build_task_card",
    "build_unified_registry",
    "is_valid_transition",
    "validate_transition",
]
