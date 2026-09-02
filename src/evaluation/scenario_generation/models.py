"""Formal data models for the scenario pipeline.

Every source of evaluation data — historical reference cases, generated
candidate batches and future API generations — flows through exactly these
models:

    ScenarioTask -> ScenarioKernel -> EffectSpec -> CompiledCase

``ScenarioTask`` is a lightweight task card; it never requires a complete
runnable case.  Original source material is carried as read-only
``reference_material`` with provenance, never as the task payload.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..business_protocol.models import AgentInput, ReferenceBehavior, ToolConditionalStateUpdate, ToolStateBinding
from ..business_protocol.scoring_contract import PATH_PATTERN
from .catalog import load_evaluation_catalog


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_json(value: Any) -> str:
    """Serialize a value deterministically for content hashing."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Version constants
# ---------------------------------------------------------------------------

SCENARIO_TASK_VERSION = "scenario_task_v1"
SCENARIO_KERNEL_VERSION = "scenario_kernel_v1"
SCENARIO_KERNEL_DRAFT_VERSION = "scenario_kernel_draft_v1"
EFFECT_SPEC_VERSION = "effect_spec_v1"
EFFECT_SPEC_DRAFT_VERSION = "effect_spec_draft_v1"
COMPILED_CASE_VERSION = "compiled_case_v1"
SCENARIO_REGISTRY_VERSION = "scenario_registry_v1"
SCENARIO_REGISTRY_EVENT_VERSION = "scenario_registry_event_v1"
SCENARIO_ORCHESTRATOR_VERSION = "scenario_orchestrator_v1"


# ---------------------------------------------------------------------------
# Pipeline stages and the strict transition table
# ---------------------------------------------------------------------------

PipelineStage = Literal[
    "TASK_READY",
    "KERNEL_DRAFT",
    "KERNEL_NEEDS_REVISION",
    "KERNEL_READY",
    "EFFECT_DRAFT",
    "EFFECT_NEEDS_REVISION",
    "EFFECT_READY",
    "COMPILED",
    "PATH_VALID",
    "RUNTIME_VALID",
    "SEMANTIC_ACCEPTED",
    "HUMAN_ACCEPTED",
    "FROZEN",
    "REJECTED",
    "GENERATION_FAILED",
    "VALIDATION_FAILED",
]

_STAGE_ORDER: dict[PipelineStage, int] = {
    "TASK_READY": 0,
    "KERNEL_DRAFT": 1,
    "KERNEL_NEEDS_REVISION": 1,
    "KERNEL_READY": 1,
    "EFFECT_DRAFT": 2,
    "EFFECT_NEEDS_REVISION": 2,
    "EFFECT_READY": 2,
    "COMPILED": 3,
    "PATH_VALID": 4,
    "RUNTIME_VALID": 5,
    "SEMANTIC_ACCEPTED": 6,
    "HUMAN_ACCEPTED": 7,
    "FROZEN": 8,
    "REJECTED": 100,
    "GENERATION_FAILED": 101,
    "VALIDATION_FAILED": 102,
}

_ALLOWED_TRANSITIONS: dict[PipelineStage | None, set[PipelineStage]] = {
    None: {"TASK_READY"},
    "TASK_READY": {"KERNEL_DRAFT", "KERNEL_READY", "GENERATION_FAILED", "REJECTED"},
    "KERNEL_DRAFT": {"KERNEL_READY", "KERNEL_NEEDS_REVISION", "VALIDATION_FAILED", "REJECTED"},
    "KERNEL_NEEDS_REVISION": {"KERNEL_DRAFT", "KERNEL_READY", "REJECTED"},
    "KERNEL_READY": {"EFFECT_DRAFT", "EFFECT_READY", "GENERATION_FAILED", "REJECTED"},
    "EFFECT_DRAFT": {"EFFECT_READY", "EFFECT_NEEDS_REVISION", "VALIDATION_FAILED", "REJECTED"},
    "EFFECT_NEEDS_REVISION": {"EFFECT_DRAFT", "EFFECT_READY", "REJECTED"},
    "EFFECT_READY": {"COMPILED", "VALIDATION_FAILED", "REJECTED"},
    "COMPILED": {"PATH_VALID", "VALIDATION_FAILED", "REJECTED"},
    "PATH_VALID": {"RUNTIME_VALID", "VALIDATION_FAILED", "REJECTED"},
    "RUNTIME_VALID": {"SEMANTIC_ACCEPTED", "REJECTED"},
    "SEMANTIC_ACCEPTED": {"HUMAN_ACCEPTED", "REJECTED"},
    "HUMAN_ACCEPTED": {"FROZEN", "REJECTED"},
    "FROZEN": set(),
    "REJECTED": set(),
    "GENERATION_FAILED": {"KERNEL_DRAFT", "EFFECT_DRAFT", "REJECTED"},
    "VALIDATION_FAILED": {"KERNEL_NEEDS_REVISION", "EFFECT_NEEDS_REVISION", "REJECTED"},
}

TERMINAL_STAGES: frozenset[PipelineStage] = frozenset({"FROZEN", "REJECTED"})


def validate_transition(current: PipelineStage | None, target: PipelineStage) -> None:
    if target not in _ALLOWED_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid pipeline transition: {current} -> {target}")


def stage_order(stage: PipelineStage) -> int:
    return _STAGE_ORDER[stage]


# ---------------------------------------------------------------------------
# Task card (lightweight input envelope)
# ---------------------------------------------------------------------------

TaskOrigin = Literal["reference", "candidate", "generated", "manual"]


class TaskProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    origin: TaskOrigin
    source_path: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    model_id: str | None = None
    seed: int | str | None = None
    prompt_version: str | None = None
    created_at: str = Field(default_factory=_now)


class ReferenceMaterial(BaseModel):
    """Read-only source material attached to a task; never the task payload."""

    model_config = ConfigDict(extra="forbid")

    ref_id: str = Field(min_length=1, max_length=120)
    kind: Literal["case_jsonl", "case_json", "candidate_batch", "text"]
    source_path: str = Field(min_length=1)
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    notes: list[str] = Field(default_factory=list)


class ScenarioTask(BaseModel):
    """Lightweight task card: objective and constraints only.

    The task never embeds a complete runnable case.  The original full case,
    when it exists, is kept as :attr:`reference_material` under the raw,
    read-only data root and referenced by path and hash.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_task_v1"] = SCENARIO_TASK_VERSION
    task_id: str = Field(pattern=r"^task-[a-z0-9-]{12,100}$")
    branch_id: str = Field(min_length=1, max_length=40)
    category: str = Field(pattern=r"^[A-Z]{3}$")
    subtype: str | None = Field(default=None, max_length=40)
    objective: str = Field(min_length=8, max_length=2000)
    mechanism_requirements: list[str] = Field(min_length=1)
    scenario_constraints: dict[str, Any] = Field(default_factory=dict)
    forbidden_patterns: list[str] = Field(default_factory=list)
    dedup_constraints: dict[str, Any] = Field(default_factory=dict)
    reference_material: list[ReferenceMaterial] = Field(default_factory=list)
    provenance: TaskProvenance
    lineage: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_branch(self) -> "ScenarioTask":
        catalog = load_evaluation_catalog()
        branch = catalog.branch_for_id(self.branch_id)
        if branch.category != self.category:
            raise ValueError(
                f"task {self.task_id}: branch {self.branch_id} belongs to "
                f"category {branch.category}, got {self.category}"
            )
        if branch.subtype != self.subtype:
            raise ValueError(
                f"task {self.task_id}: branch {self.branch_id} has subtype "
                f"{branch.subtype!r}, got {self.subtype!r}"
            )
        return self

    @classmethod
    def create(
        cls,
        *,
        task_id: str,
        branch_id: str,
        objective: str,
        mechanism_requirements: list[str],
        subtype: str | None = None,
        scenario_constraints: dict[str, Any] | None = None,
        forbidden_patterns: list[str] | None = None,
        dedup_constraints: dict[str, Any] | None = None,
        provenance: TaskProvenance,
        reference_material: list[ReferenceMaterial] | None = None,
        lineage: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> "ScenarioTask":
        catalog = load_evaluation_catalog()
        branch = catalog.branch_for_id(branch_id)
        if subtype is None:
            subtype = branch.subtype
        task = cls(
            task_id=task_id,
            branch_id=branch_id,
            category=branch.category,
            subtype=subtype,
            objective=objective,
            mechanism_requirements=list(mechanism_requirements),
            scenario_constraints=scenario_constraints or {},
            forbidden_patterns=forbidden_patterns or [],
            dedup_constraints=dedup_constraints or {},
            reference_material=reference_material or [],
            provenance=provenance,
            lineage=lineage or {},
            metadata=metadata or {},
        )
        return seal_task(task)


def seal_task(task: ScenarioTask) -> ScenarioTask:
    return task.model_copy(
        update={"content_sha256": _digest(task.model_copy(update={"content_sha256": None}))}
    )


def verify_task_hash(task: ScenarioTask) -> str:
    if not task.content_sha256:
        raise ValueError(f"task {task.task_id} is not sealed")
    actual = _digest(task.model_copy(update={"content_sha256": None}))
    if actual != task.content_sha256:
        raise ValueError(f"task {task.task_id} hash mismatch")
    return actual


# ---------------------------------------------------------------------------
# ScenarioKernel
# ---------------------------------------------------------------------------

class KernelSource(BaseModel):
    """Immutable provenance for a kernel extraction or generation."""

    model_config = ConfigDict(extra="forbid")

    source_kind: Literal["generated", "reference_extracted", "manual"]
    source_candidate_uid: str | None = None
    source_case_id: str | None = None
    source_path: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    generator_model_id: str | None = None
    generation_seed: int | str | None = None
    prompt_version: str | None = None
    extracted_at: str = Field(default_factory=_now)


class KernelRole(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_id: str = Field(min_length=1, max_length=100)
    responsibility: str = Field(min_length=1, max_length=500)


class KernelStep(BaseModel):
    """Business-visible portion of one main or recovery step."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=100)
    upstream_step_ids: list[str] = Field(default_factory=list)
    role_id: str = Field(min_length=1, max_length=100)
    role_responsibility: str = Field(min_length=1, max_length=500)
    task_id: str = Field(min_length=1, max_length=100)
    task_content: str = Field(min_length=1, max_length=2000)
    current_time: str = Field(min_length=1, max_length=80)
    business_object: str = Field(min_length=1, max_length=300)
    inputs: dict[Literal["normal", "risk", "recovery"], AgentInput]
    raw_user_message: str | None = None
    history_fixtures: dict[Literal["normal", "risk", "recovery"], list[dict[str, Any]]] = Field(
        default_factory=dict
    )
    current_times: dict[Literal["normal", "risk", "recovery"], str] = Field(default_factory=dict)
    safe_behavior: str = Field(min_length=1, max_length=2000)
    unsafe_behavior: str = Field(min_length=1, max_length=2000)
    recovery_source_step_ids: list[str] = Field(default_factory=list)
    key_node_fixture: bool = True
    key_node_target: bool = True
    visible_state_paths: list[str] = Field(default_factory=list)
    observable_risk_impacts: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_inputs(self) -> "KernelStep":
        is_recovery = "recovery" in self.inputs
        expected = {"recovery"} if is_recovery else {"normal", "risk"}
        if set(self.inputs) != expected:
            raise ValueError(
                f"kernel step {self.step_id} inputs must be {sorted(expected)}"
            )
        if len(self.upstream_step_ids) != len(set(self.upstream_step_ids)):
            raise ValueError(f"kernel step {self.step_id} has duplicate upstream IDs")
        if len(self.recovery_source_step_ids) != len(set(self.recovery_source_step_ids)):
            raise ValueError(f"kernel step {self.step_id} has duplicate recovery source IDs")
        if not is_recovery and self.recovery_source_step_ids:
            raise ValueError(f"main kernel step {self.step_id} cannot declare recovery source IDs")
        if not set(self.history_fixtures).issubset(expected):
            raise ValueError(f"kernel step {self.step_id} history conditions must be within {sorted(expected)}")
        if not set(self.current_times).issubset(expected):
            raise ValueError(f"kernel step {self.step_id} current-time conditions must be within {sorted(expected)}")
        invalid_paths = [
            path for path in self.visible_state_paths if not PATH_PATTERN.fullmatch(path)
        ]
        if invalid_paths:
            raise ValueError(f"kernel step {self.step_id} has invalid visible state paths: {invalid_paths}")
        return self


class ScenarioKernel(BaseModel):
    """Stable semantic intermediate representation for one scenario."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_kernel_v1"] = SCENARIO_KERNEL_VERSION
    kernel_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{3,120}$")
    category: str = Field(min_length=2, max_length=100)
    subtype: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=4, max_length=300)
    purpose: str = Field(min_length=1, max_length=1000)
    business_domain: str = Field(min_length=1, max_length=200)
    business_object: str = Field(min_length=1, max_length=500)
    roles: list[KernelRole] = Field(min_length=1)
    steps: list[KernelStep] = Field(min_length=1)
    recovery_steps: list[KernelStep] = Field(default_factory=list)
    initial_state: dict[Literal["normal", "risk", "recovery"], dict[str, Any]]
    causal_variable: str = Field(min_length=1, max_length=1000)
    normal_evidence_summary: str = Field(min_length=1, max_length=2000)
    risk_evidence_summary: str = Field(min_length=1, max_length=2000)
    risk_consequences: list[str] = Field(min_length=1)
    recovery_goal: str = Field(min_length=1, max_length=2000)
    execution_plan: "AuthoringExecutionPlan" = Field(default_factory=lambda: AuthoringExecutionPlan())
    source: KernelSource
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        catalog = load_evaluation_catalog()
        if value not in (*catalog.category_codes, *catalog.category_names_zh):
            raise ValueError(f"unknown evaluation category: {value}")
        return value

    @model_validator(mode="after")
    def validate_structure(self) -> "ScenarioKernel":
        ids = [step.step_id for step in [*self.steps, *self.recovery_steps]]
        if len(ids) != len(set(ids)):
            raise ValueError("kernel step IDs must be globally unique")
        role_ids = {role.role_id for role in self.roles}
        unknown_roles = sorted({step.role_id for step in [*self.steps, *self.recovery_steps]} - role_ids)
        if unknown_roles:
            raise ValueError(f"kernel references unknown roles: {unknown_roles}")
        main_ids = {step.step_id for step in self.steps}
        for step in self.steps:
            unknown = sorted(set(step.upstream_step_ids) - main_ids)
            if unknown:
                raise ValueError(f"kernel step {step.step_id} has unknown upstream IDs: {unknown}")
        for step in self.recovery_steps:
            unknown_sources = sorted(set(step.recovery_source_step_ids) - main_ids)
            if unknown_sources:
                raise ValueError(f"recovery kernel step {step.step_id} references unknown risk sources: {unknown_sources}")
            if not step.recovery_source_step_ids and self.source.source_kind != "reference_extracted":
                raise ValueError(f"recovery kernel step {step.step_id} must reference a main risk source")
        if set(self.initial_state) != {"normal", "risk", "recovery"}:
            raise ValueError("kernel initial_state must contain normal, risk and recovery")
        if self.source.source_kind != "reference_extracted":
            if not self.recovery_steps:
                raise ValueError("generated ScenarioKernel must declare at least one recovery step")
            for step in [*self.steps, *self.recovery_steps]:
                if len(step.role_responsibility.strip()) < 4:
                    raise ValueError(f"kernel step {step.step_id} role_responsibility is too short")
                if len(step.task_content.strip()) < 8:
                    raise ValueError(f"kernel step {step.step_id} task_content is too short")
                if len(step.safe_behavior.strip()) < 4 or len(step.unsafe_behavior.strip()) < 4:
                    raise ValueError(f"kernel step {step.step_id} must describe safe and unsafe behaviour")
        return self


class ScenarioKernelDraft(BaseModel):
    """Model-facing kernel payload before local identity/provenance is added."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_kernel_draft_v1"] = SCENARIO_KERNEL_DRAFT_VERSION
    category: str = Field(min_length=2, max_length=100)
    subtype: str | None = Field(default=None, max_length=100)
    title: str = Field(min_length=4, max_length=300)
    purpose: str = Field(min_length=8, max_length=1000)
    business_domain: str = Field(min_length=2, max_length=200)
    business_object: str = Field(min_length=2, max_length=500)
    roles: list[KernelRole] = Field(min_length=1)
    steps: list[KernelStep] = Field(min_length=1)
    recovery_steps: list[KernelStep] = Field(min_length=1)
    initial_state: dict[Literal["normal", "risk", "recovery"], dict[str, Any]]
    causal_variable: str = Field(min_length=8, max_length=1000)
    normal_evidence_summary: str = Field(min_length=8, max_length=2000)
    risk_evidence_summary: str = Field(min_length=8, max_length=2000)
    risk_consequences: list[str] = Field(min_length=1)
    recovery_goal: str = Field(min_length=8, max_length=2000)
    execution_plan: "AuthoringExecutionPlan" = Field(default_factory=lambda: AuthoringExecutionPlan())
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("category")
    @classmethod
    def validate_category(cls, value: str) -> str:
        catalog = load_evaluation_catalog()
        if value not in (*catalog.category_codes, *catalog.category_names_zh):
            raise ValueError(f"unknown evaluation category: {value}")
        return value

    @model_validator(mode="after")
    def validate_draft(self) -> "ScenarioKernelDraft":
        main_ids = {step.step_id for step in self.steps}
        for step in self.recovery_steps:
            unknown = sorted(set(step.recovery_source_step_ids) - main_ids)
            if unknown:
                raise ValueError(f"recovery step {step.step_id} references unknown main steps: {unknown}")
            if not step.recovery_source_step_ids:
                raise ValueError(f"recovery step {step.step_id} must declare recovery_source_step_ids")
        if set(self.initial_state) != {"normal", "risk", "recovery"}:
            raise ValueError("kernel draft initial_state must contain normal, risk and recovery")
        return self


def seal_kernel(kernel: ScenarioKernel) -> ScenarioKernel:
    digest = _content_hash(kernel, {"content_sha256"})
    return kernel.model_copy(update={"content_sha256": digest})


def verify_kernel_hash(kernel: ScenarioKernel) -> str:
    if not kernel.content_sha256:
        raise ValueError(f"kernel {kernel.kernel_id} is not sealed")
    actual = _content_hash(kernel, {"content_sha256"})
    if actual != kernel.content_sha256:
        raise ValueError(f"kernel {kernel.kernel_id} hash mismatch: expected {kernel.content_sha256}, actual {actual}")
    return actual


# ---------------------------------------------------------------------------
# EffectSpec
# ---------------------------------------------------------------------------

class EffectSpecDraft(BaseModel):
    """Model-facing executable-effect payload bound to a kernel digest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["effect_spec_draft_v1"] = EFFECT_SPEC_DRAFT_VERSION
    kernel_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{3,120}$")
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    steps: list["EffectStepSpec"] = Field(min_length=1)
    safe_final_states: list["FinalStatePattern"] = Field(min_length=1)
    unsafe_final_states: list["FinalStatePattern"] = Field(min_length=1)
    execution_plan: "AuthoringExecutionPlan" = Field(default_factory=lambda: AuthoringExecutionPlan())
    notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_draft(self) -> "EffectSpecDraft":
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("effect draft step IDs must be unique")
        if any(not step.tools for step in self.steps):
            raise ValueError("effect draft requires at least one tool per step")
        return self


class EffectToolSpec(BaseModel):
    """Compact, condition-independent executable tool effect."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    description: str = Field(min_length=1, max_length=500)
    kind: Literal["observe", "request", "commit", "recover"]
    parameters: dict[str, Any]
    response: dict[str, Any]
    response_overrides: dict[Literal["normal", "risk", "recovery"], dict[str, Any]] = Field(default_factory=dict)
    fixed_state: dict[str, Any] = Field(default_factory=dict)
    state_from_arguments: dict[str, str] = Field(default_factory=dict)
    state_from_response: dict[str, str] = Field(default_factory=dict)
    state_bindings: list[ToolStateBinding] = Field(default_factory=list)
    conditional_state_updates: list[ToolConditionalStateUpdate] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    # Reference extraction may preserve condition-specific effects which cannot
    # be safely compressed into the normalized authoring representation.  Such
    # an EffectSpec is explicitly draft-only and cannot be compiled.
    condition_effects: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> "EffectToolSpec":
        if self.parameters.get("type") != "object":
            raise ValueError(f"effect tool {self.name} parameters must be an object schema")
        if self.condition_effects is not None:
            return self
        targets = [*self.fixed_state, *self.state_from_arguments, *self.state_from_response]
        invalid_targets = [path for path in targets if not PATH_PATTERN.fullmatch(path)]
        if invalid_targets:
            raise ValueError(f"effect tool {self.name} has invalid state paths: {invalid_targets}")
        sources = [*self.state_from_arguments.values(), *self.state_from_response.values()]
        invalid_sources = [path for path in sources if not PATH_PATTERN.fullmatch(path)]
        if invalid_sources:
            raise ValueError(f"effect tool {self.name} has invalid source paths: {invalid_sources}")
        target_paths = [*self.fixed_state, *self.state_from_arguments, *self.state_from_response]
        for index, first in enumerate(target_paths):
            for second in target_paths[index + 1 :]:
                if _paths_overlap(first, second):
                    raise ValueError(f"effect tool {self.name} writes overlapping state paths {first} and {second}")
        return self

    def to_authoring_tool(self) -> "AuthoringToolSpec":
        if self.condition_effects is not None:
            raise ValueError(f"source effect tool {self.name} must be semantically repaired before compile")
        return AuthoringToolSpec.model_validate(
            self.model_dump(mode="json", exclude={"condition_effects", "kind"})
        )


class EffectStepSpec(BaseModel):
    """Tool effects and observable behaviour alternatives for one step."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=100)
    tools: list[EffectToolSpec] = Field(default_factory=list)
    behavior: "StepBehaviorOracle | None" = None
    scored: bool = True
    objective_unsafe_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tool_names(self) -> "EffectStepSpec":
        names = [tool.name for tool in self.tools]
        unknown_unsafe = sorted(set(self.objective_unsafe_tools) - set(names))
        if unknown_unsafe:
            raise ValueError(f"effect step {self.step_id} references unknown unsafe tools: {unknown_unsafe}")
        return self


class EffectSpec(BaseModel):
    """Versioned executable-effect specification bound to one kernel."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["effect_spec_v1"] = EFFECT_SPEC_VERSION
    effect_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{3,120}$")
    kernel_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{3,120}$")
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: Literal["DRAFT", "READY_FOR_COMPILE"] = "DRAFT"
    steps: list[EffectStepSpec] = Field(min_length=1)
    safe_final_states: list["FinalStatePattern"] = Field(default_factory=list)
    unsafe_final_states: list["FinalStatePattern"] = Field(default_factory=list)
    execution_plan: "AuthoringExecutionPlan" = Field(default_factory=lambda: AuthoringExecutionPlan())
    notes: list[str] = Field(default_factory=list)
    source: KernelSource
    metadata: dict[str, Any] = Field(default_factory=dict)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_structure(self) -> "EffectSpec":
        ids = [step.step_id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("effect step IDs must be unique")
        if self.status == "READY_FOR_COMPILE":
            duplicate_steps = [
                step.step_id
                for step in self.steps
                if len([tool.name for tool in step.tools]) != len({tool.name for tool in step.tools})
            ]
            if duplicate_steps:
                raise ValueError(f"ready effect spec cannot contain duplicate tool names: {duplicate_steps}")
            source_tools = [tool.name for step in self.steps for tool in step.tools if tool.condition_effects is not None]
            if source_tools:
                raise ValueError(f"ready effect spec cannot contain unresolved condition effects: {source_tools}")
            empty_steps = [step.step_id for step in self.steps if not step.tools]
            if empty_steps:
                raise ValueError(f"ready effect spec requires at least one tool per step: {empty_steps}")
            missing = [step.step_id for step in self.steps if step.scored and step.behavior is None]
            if missing:
                raise ValueError(f"ready effect spec requires behaviour for every scored step: {missing}")
            if not self.safe_final_states or not self.unsafe_final_states:
                raise ValueError("ready effect spec requires safe and unsafe final-state rules")
        return self


def seal_effect_spec(effect: EffectSpec) -> EffectSpec:
    digest = _content_hash(effect, {"content_sha256"})
    return effect.model_copy(update={"content_sha256": digest})


def verify_effect_spec_hash(effect: EffectSpec) -> str:
    if not effect.content_sha256:
        raise ValueError(f"effect spec {effect.effect_id} is not sealed")
    actual = _content_hash(effect, {"content_sha256"})
    if actual != effect.content_sha256:
        raise ValueError(f"effect spec {effect.effect_id} hash mismatch: expected {effect.content_sha256}, actual {actual}")
    return actual


def verify_effect_kernel_binding(kernel: ScenarioKernel, effect: EffectSpec) -> None:
    kernel_hash = verify_kernel_hash(kernel)
    if effect.kernel_id != kernel.kernel_id:
        raise ValueError(f"effect {effect.effect_id} references kernel {effect.kernel_id}, expected {kernel.kernel_id}")
    if effect.kernel_sha256 != kernel_hash:
        raise ValueError(f"effect {effect.effect_id} kernel hash does not match kernel {kernel.kernel_id}")


def _content_hash(model: BaseModel, excluded: set[str]) -> str:
    payload = model.model_dump(mode="json", exclude=excluded)
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# CompiledCase
# ---------------------------------------------------------------------------

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
    case: Any
    compiled_at: str = Field(default_factory=_now)
    content_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


def seal_compiled_case(case: CompiledCase) -> CompiledCase:
    return case.model_copy(
        update={"content_sha256": _digest(case.model_copy(update={"content_sha256": None}))}
    )


def verify_compiled_case_hash(case: CompiledCase) -> str:
    if not case.content_sha256:
        raise ValueError(f"compiled case {case.case_id} is not sealed")
    actual = _digest(case.model_copy(update={"content_sha256": None}))
    if actual != case.content_sha256:
        raise ValueError(f"compiled case {case.case_id} hash mismatch")
    return actual


# ---------------------------------------------------------------------------
# Authoring models (the compiler backend vocabulary)
# ---------------------------------------------------------------------------

QUERY_TOOL_PREFIXES = (
    "query_", "get_", "fetch_", "read_", "lookup_", "verify_", "inspect_", "check_",
)
SCHEMA_PLACEHOLDER_STRINGS = {
    "string", "number", "integer", "boolean", "array", "object", "unknown",
    "placeholder", "tbd", "todo", "<string>", "<number>", "<boolean>",
}


def _paths_overlap(first: str, second: str) -> bool:
    return first == second or first.startswith(f"{second}.") or second.startswith(f"{first}.")


def _flatten_value(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict) and value:
        result: dict[str, Any] = {}
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten_value(item, child))
        return result
    return {prefix: deepcopy(value)} if prefix else {}


def _contains_list(value: Any) -> bool:
    if isinstance(value, list):
        return True
    if isinstance(value, dict):
        return any(_contains_list(item) for item in value.values())
    return False


def _contains_template_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return ("{{" in value and "}}" in value) or "<" in value and ">" in value
    if isinstance(value, dict):
        return any(_contains_template_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_template_placeholder(item) for item in value)
    return False


def _contains_schema_placeholder(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in SCHEMA_PLACEHOLDER_STRINGS
    if isinstance(value, dict):
        return any(_contains_schema_placeholder(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_schema_placeholder(item) for item in value)
    return False


def _has_query_domain_facts(value: Any, key: str = "") -> bool:
    if isinstance(value, dict):
        for child_key, child in value.items():
            if _has_query_domain_facts(child, str(child_key)):
                return True
        return False
    if isinstance(value, list):
        return any(_has_query_domain_facts(item, key) for item in value)
    if isinstance(value, bool) or (isinstance(value, (int, float)) and not isinstance(value, bool)):
        return True
    if isinstance(value, str) and value.strip() and key.lower() not in {
        "execution_status", "event_id", "request_id", "status", "tool_call_id",
    }:
        lowered = value.strip().lower()
        return lowered not in SCHEMA_PLACEHOLDER_STRINGS
    return False


def _strip_one_prefix(value: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if value.startswith(prefix):
            return value[len(prefix):]
    return value


class AuthoringToolSpec(BaseModel):
    """One tool with a shared response plus optional condition-specific facts."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{1,80}$")
    description: str = Field(min_length=8, max_length=500)
    parameters: dict[str, Any]
    response: dict[str, Any]
    response_overrides: dict[Literal["normal", "risk", "recovery"], dict[str, Any]] = Field(default_factory=dict)
    fixed_state: dict[str, Any] = Field(default_factory=dict)
    state_from_arguments: dict[str, str] = Field(default_factory=dict)
    state_from_response: dict[str, str] = Field(default_factory=dict)
    state_bindings: list[ToolStateBinding] = Field(default_factory=list)
    conditional_state_updates: list[ToolConditionalStateUpdate] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"

    @model_validator(mode="before")
    @classmethod
    def normalize_binding_source_paths(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        normalized = deepcopy(value)
        for field, prefixes in (
            ("state_from_arguments", ("arguments.", "parameters.")),
            ("state_from_response", ("response.",)),
        ):
            mapping = normalized.get(field)
            if not isinstance(mapping, dict):
                continue
            normalized[field] = {
                target: _strip_one_prefix(str(source), prefixes)
                for target, source in mapping.items()
            }
        return normalized

    @model_validator(mode="after")
    def validate_paths(self) -> "AuthoringToolSpec":
        if self.parameters.get("type") != "object":
            raise ValueError(f"tool {self.name} parameters must be an object schema")
        target_paths = [*self.fixed_state, *self.state_from_arguments, *self.state_from_response]
        invalid_targets = [path for path in target_paths if not PATH_PATTERN.fullmatch(path)]
        if invalid_targets:
            raise ValueError(f"tool {self.name} has invalid state paths: {invalid_targets}")
        source_paths = [*self.state_from_arguments.values(), *self.state_from_response.values()]
        invalid_sources = [path for path in source_paths if not PATH_PATTERN.fullmatch(path)]
        if invalid_sources:
            raise ValueError(f"tool {self.name} has invalid source paths: {invalid_sources}")
        for index, first in enumerate(target_paths):
            for second in target_paths[index + 1 :]:
                if _paths_overlap(first, second):
                    raise ValueError(f"tool {self.name} writes overlapping state paths {first} and {second}")
        if _contains_template_placeholder(self.response) or any(
            _contains_template_placeholder(item) for item in self.response_overrides.values()
        ):
            raise ValueError(f"tool {self.name} responses must contain concrete values, not templates")
        if _contains_schema_placeholder(self.response) or any(
            _contains_schema_placeholder(item) for item in self.response_overrides.values()
        ):
            raise ValueError(f"tool {self.name} responses must contain business values, not type placeholders")
        if self.name.startswith(QUERY_TOOL_PREFIXES):
            responses = [self.response, *self.response_overrides.values()]
            if not any(_has_query_domain_facts(item) for item in responses):
                raise ValueError(f"query tool {self.name} must return concrete domain facts, not only status or IDs")
        return self


class AuthoringStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=100)
    upstream_step_ids: list[str] = Field(default_factory=list)
    role_id: str = Field(min_length=1, max_length=100)
    role_responsibility: str = Field(min_length=4, max_length=500)
    task_id: str = Field(min_length=1, max_length=100)
    task_content: str = Field(min_length=8, max_length=2000)
    current_time: str
    current_times: dict[Literal["normal", "risk", "recovery"], str] = Field(default_factory=dict)
    business_object: str = Field(min_length=1, max_length=300)
    visible_state_paths: list[str] = Field(default_factory=list)
    inputs: dict[str, AgentInput]
    raw_user_message: str | None = None
    history_fixtures: dict[Literal["normal", "risk", "recovery"], list[dict[str, Any]]] = Field(default_factory=dict)
    tools: list[AuthoringToolSpec] = Field(min_length=1)
    reference: ReferenceBehavior
    key_node_fixture: bool = True
    key_node_target: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_unique_tools(self) -> "AuthoringStepSpec":
        names = [tool.name for tool in self.tools]
        if len(names) != len(set(names)):
            raise ValueError(f"step {self.step_id} has duplicate tool names")
        for condition, messages in self.history_fixtures.items():
            for index, message in enumerate(messages):
                if set(message) != {"role", "content"}:
                    raise ValueError(f"step {self.step_id}/{condition} history message {index} must contain role and content only")
                if message["role"] not in {"user", "assistant"}:
                    raise ValueError(f"step {self.step_id}/{condition} history role must be user or assistant")
                if not isinstance(message["content"], str) or not message["content"].strip():
                    raise ValueError(f"step {self.step_id}/{condition} history content must be non-empty text")
        allowed_conditions = {"recovery"} if "recovery" in self.inputs else {"normal", "risk"}
        invalid_times = sorted(set(self.current_times) - allowed_conditions)
        if invalid_times:
            raise ValueError(f"step {self.step_id} current_times contain invalid conditions: {invalid_times}")
        return self


class AuthoringExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pairing: Literal["independent", "shared_prefix"] = "independent"
    shared_prefix_step_ids: list[str] = Field(default_factory=list)
    normal_state_overrides: dict[str, Any] = Field(default_factory=dict)
    recovery_policy: Literal["on_risk_unsafe", "always", "never"] = "on_risk_unsafe"
    recovery_step_ids: list[str] | None = None


class AuthoringCaseSpec(BaseModel):
    """Concise case fields; identity and scoring contract are injected locally."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=4, max_length=300)
    purpose: str = Field(min_length=8, max_length=1000)
    steps: list[AuthoringStepSpec] = Field(min_length=1)
    recovery_steps: list[AuthoringStepSpec] = Field(min_length=1)
    initial_state: dict[str, dict[str, Any]]
    execution_plan: AuthoringExecutionPlan = Field(default_factory=AuthoringExecutionPlan)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_condition_names(self) -> "AuthoringCaseSpec":
        if set(self.initial_state) != {"normal", "risk", "recovery"}:
            raise ValueError("initial_state must contain normal, risk and recovery")
        all_ids = [step.step_id for step in [*self.steps, *self.recovery_steps]]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("authoring case has duplicate step IDs")
        for step in self.steps:
            if set(step.inputs) != {"normal", "risk"}:
                raise ValueError(f"main step {step.step_id} inputs must contain normal and risk")
            if not set(step.history_fixtures).issubset({"normal", "risk"}):
                raise ValueError(f"main step {step.step_id} histories may contain normal and risk only")
        for step in self.recovery_steps:
            if set(step.inputs) != {"recovery"}:
                raise ValueError(f"recovery step {step.step_id} inputs must contain recovery only")
            if not set(step.history_fixtures).issubset({"recovery"}):
                raise ValueError(f"recovery step {step.step_id} histories may contain recovery only")
            if not set(step.upstream_step_ids).intersection(item.step_id for item in self.steps):
                raise ValueError(f"recovery step {step.step_id} must reference a main step that produced the risk")
        return self


class ToolCallCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    argument_not_equals: dict[str, Any] = Field(default_factory=dict)
    simulation_arguments: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_criteria(self) -> "ToolCallCriterion":
        if _contains_list(self.arguments) or _contains_list(self.argument_not_equals):
            raise ValueError(
                "scoring call criteria must not match arrays by index; use tool presence, "
                "a scalar decision field, or simulation_arguments"
            )
        equals_paths = set(_flatten_value(self.arguments))
        not_equals_paths = set(_flatten_value(self.argument_not_equals))
        overlap = sorted(equals_paths & not_equals_paths)
        if overlap:
            raise ValueError(f"tool call criterion cannot require equals and not-equals on {overlap}")
        return self


class BehaviorPattern(BaseModel):
    """All listed calls and argument values must be observed in one step."""

    model_config = ConfigDict(extra="forbid")

    calls: list[ToolCallCriterion] = Field(min_length=1)


class ConditionBehaviorOracle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    safe: list[BehaviorPattern] = Field(min_length=1)
    unsafe: list[BehaviorPattern] = Field(min_length=1)


class StepBehaviorOracle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    normal: ConditionBehaviorOracle | None = None
    risk: ConditionBehaviorOracle | None = None
    recovery: ConditionBehaviorOracle | None = None


class FinalStateCriterion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_path: str
    operator: str = "equals"
    expected: Any = None

    @model_validator(mode="before")
    @classmethod
    def normalize_operator_shorthand(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "operator" in value:
            return value
        operator_keys = [
            key
            for key in ("equals", "not_equals", "in", "not_in", "contains", "exists", "not_exists", "truthy", "falsy")
            if key in value
        ]
        if len(operator_keys) != 1:
            return value
        operator = operator_keys[0]
        normalized = {key: item for key, item in value.items() if key != operator}
        normalized["operator"] = operator
        if operator not in {"exists", "not_exists", "truthy", "falsy"}:
            normalized["expected"] = value[operator]
        return normalized

    @model_validator(mode="after")
    def validate_shape(self) -> "FinalStateCriterion":
        if not PATH_PATTERN.fullmatch(self.field_path):
            raise ValueError("final-state field_path must be a dotted data path")
        if any(segment.isdigit() for segment in self.field_path.split(".")):
            raise ValueError("final-state rules must not address arrays by numeric index; use contains on the array field or a scalar summary field")
        if self.operator in {"exists", "not_exists", "truthy", "falsy"}:
            if self.expected is not None:
                raise ValueError(f"operator {self.operator} must omit expected")
        elif "expected" not in self.model_fields_set:
            raise ValueError(f"operator {self.operator} requires expected")
        return self


class FinalStatePattern(BaseModel):
    model_config = ConfigDict(extra="forbid")

    match: Literal["all", "any"] = "all"
    criteria: list[FinalStateCriterion] = Field(min_length=1)
    conditions: list[Literal["normal", "risk", "recovery"]] = Field(
        default_factory=lambda: ["normal", "risk", "recovery"]
    )

    @model_validator(mode="after")
    def validate_conditions(self) -> "FinalStatePattern":
        if not self.conditions:
            raise ValueError("final-state pattern must target at least one condition")
        if len(self.conditions) != len(set(self.conditions)):
            raise ValueError("final-state pattern conditions must be unique")
        return self


class AuthoringScoringOracle(BaseModel):
    """Small observable oracle compiled into the full scoring contract."""

    model_config = ConfigDict(extra="forbid")

    step_behaviors: list[StepBehaviorOracle] = Field(min_length=1)
    safe_final_states: list[FinalStatePattern] = Field(min_length=1)
    unsafe_final_states: list[FinalStatePattern] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_steps(self) -> "AuthoringScoringOracle":
        step_ids = [item.step_id for item in self.step_behaviors]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("scoring oracle has duplicate step IDs")
        return self


GenerationStatus = Literal["COMPLETED", "FAILED_QUALITY_GATE"]


class AuthoringScenarioResponse(BaseModel):
    """Minimal JSON object returned by a generation model."""

    model_config = ConfigDict(extra="forbid")

    prompt_version: Literal["ioa_scenario_generation_v7_authoring"]
    generation_status: GenerationStatus
    case: AuthoringCaseSpec | None = None
    scoring_oracle: AuthoringScoringOracle | None = None
    known_open_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_status(self) -> "AuthoringScenarioResponse":
        if self.generation_status == "COMPLETED":
            if self.case is None or self.scoring_oracle is None:
                raise ValueError("completed response requires case and scoring_oracle")
            if self.known_open_questions:
                raise ValueError("completed response cannot contain open questions")
        else:
            if self.case is not None or self.scoring_oracle is not None:
                raise ValueError("failed response must not contain case or scoring_oracle")
            if not self.known_open_questions:
                raise ValueError("failed response must explain the quality-gate failure")
        return self


# ---------------------------------------------------------------------------
# Registry models
# ---------------------------------------------------------------------------

class ArtifactRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: str = Field(min_length=1)
    depends_on: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_relative_path(self) -> "ArtifactRef":
        normalized = self.path.replace("\\", "/")
        parsed = Path(normalized)
        if (
            normalized.startswith("/")
            or (len(normalized) >= 2 and normalized[1] == ":")
            or any(part == ".." for part in parsed.parts)
        ):
            raise ValueError("artifact path must be project-relative")
        return self


class RegistryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    case_id: str = ""
    branch_id: str = ""
    stage: PipelineStage
    generation: int = Field(default=1, ge=1)
    artifacts: dict[str, ArtifactRef] = Field(default_factory=dict)
    invalidated_artifacts: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


class RegistryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_registry_event_v1"] = SCENARIO_REGISTRY_EVENT_VERSION
    task_id: str
    from_stage: PipelineStage | None
    to_stage: PipelineStage
    generation: int = Field(ge=1)
    reason: str = Field(min_length=1)
    at: str = Field(default_factory=_now)


class ScenarioRegistry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_registry_v1"] = SCENARIO_REGISTRY_VERSION
    orchestrator_version: Literal["scenario_orchestrator_v1"] = SCENARIO_ORCHESTRATOR_VERSION
    entries: dict[str, RegistryEntry] = Field(default_factory=dict)
    events: list[RegistryEvent] = Field(default_factory=list)
    updated_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Quality records
# ---------------------------------------------------------------------------

class RuntimeCheckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["runtime_check_v1"] = "runtime_check_v1"
    task_id: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    runner_version: str = Field(min_length=1, max_length=200)
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_level_results: dict[str, Any] = Field(default_factory=dict)
    summary: str = Field(min_length=1, max_length=4000)
    evidence_paths: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_status_details(self) -> "RuntimeCheckRecord":
        if self.status == "FAIL" and not self.errors:
            raise ValueError("failed runtime check must include errors")
        return self


class ReviewDimension(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: float | None = None
    passed: bool
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[str] = Field(default_factory=list)


class SemanticReviewRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["semantic_review_v1"] = "semantic_review_v1"
    task_id: str = Field(min_length=1)
    reviewer_kind: Literal["model", "human", "external"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    decision: Literal["ACCEPT", "REVISE", "REJECT"]
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    dimensions: dict[str, ReviewDimension] = Field(min_length=1)
    key_issues: list[str] = Field(default_factory=list)
    recommendations: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence_paths: list[str] = Field(default_factory=list)
    raw_response_path: str | None = None
    recorded_at: str = Field(default_factory=_now)

    @model_validator(mode="after")
    def validate_decision(self) -> "SemanticReviewRecord":
        failed = [name for name, item in self.dimensions.items() if not item.passed]
        if self.decision == "ACCEPT" and failed:
            raise ValueError(f"semantic review ACCEPT cannot contain failed dimensions: {failed}")
        if self.decision in {"REVISE", "REJECT"} and not (self.key_issues or self.recommendations):
            raise ValueError("REVISE/REJECT semantic review must include key_issues or recommendations")
        return self


class HumanDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["human_decision_v1"] = "human_decision_v1"
    task_id: str = Field(min_length=1)
    decision: Literal["ACCEPT", "REVISE", "REJECT"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=4000)
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    compiled_case_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    release_membership: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Repair planning models
# ---------------------------------------------------------------------------

RepairDecision = Literal[
    "MODEL_REPAIR_REQUIRED",
    "REVISE_REQUIRED",
    "REWRITE_REQUIRED",
    "REJECTED",
]


class RepairOperation(BaseModel):
    """One requested change, with an explicit safety boundary."""

    model_config = ConfigDict(extra="forbid")

    operation_id: str = Field(min_length=1, max_length=120)
    kind: Literal["automatic_metadata", "output_identity", "semantic"]
    target: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=2000)
    before: Any = None
    after: Any = None
    safe_to_apply_automatically: bool = False


class RepairPlan(BaseModel):
    """Machine-readable work order for one task."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_repair_plan_v1"] = "scenario_repair_plan_v1"
    task_id: str = Field(min_length=1)
    source_case_id: str | None = None
    source_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    category: str = Field(min_length=2)
    branch_id: str | None = None
    generator_model_id: str | None = None
    kernel_id: str = Field(min_length=1)
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_id: str = Field(min_length=1)
    effect_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    effect_status: Literal["DRAFT", "READY_FOR_COMPILE"]
    deterministic_findings: list[dict[str, Any]] = Field(default_factory=list)
    automatic_operations: list[RepairOperation] = Field(default_factory=list)
    required_operations: list[RepairOperation] = Field(default_factory=list)
    decision: RepairDecision
    immutable_constraints: list[str] = Field(min_length=1)
    prompt_version: str = "scenario_effect_repair_v1"
    created_at: str = Field(default_factory=_now)


class RepairApplicationResult(BaseModel):
    """Durable result written after a repair response is considered."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["scenario_repair_result_v1"] = "scenario_repair_result_v1"
    task_id: str = Field(min_length=1)
    repair_status: Literal["PENDING", "READY_FOR_COMPILE", "FAILED", "REJECTED"]
    response_path: str | None = None
    repaired_effect_path: str | None = None
    effect_id: str | None = None
    effect_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    error_type: str | None = None
    error: str | None = None
    applied_operations: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=_now)


# ---------------------------------------------------------------------------
# Schema upgrades (explicit, never silent)
# ---------------------------------------------------------------------------

def upgrade_kernel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("schema_version")
    if version == SCENARIO_KERNEL_VERSION:
        return dict(payload)
    raise ValueError(f"unsupported ScenarioKernel schema_version {version!r}; expected {SCENARIO_KERNEL_VERSION}")


def upgrade_effect_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("schema_version")
    if version == EFFECT_SPEC_VERSION:
        return dict(payload)
    raise ValueError(f"unsupported EffectSpec schema_version {version!r}; expected {EFFECT_SPEC_VERSION}")


__all__ = [
    "ALLOWED_TRANSITIONS",
    "ArtifactRef",
    "AuthoringCaseSpec",
    "AuthoringExecutionPlan",
    "AuthoringScoringOracle",
    "AuthoringScenarioResponse",
    "AuthoringStepSpec",
    "AuthoringToolSpec",
    "BehaviorPattern",
    "COMPILED_CASE_VERSION",
    "CompiledCase",
    "ConditionBehaviorOracle",
    "EFFECT_SPEC_DRAFT_VERSION",
    "EFFECT_SPEC_VERSION",
    "EffectSpec",
    "EffectSpecDraft",
    "EffectStepSpec",
    "EffectToolSpec",
    "FinalStateCriterion",
    "FinalStatePattern",
    "HumanDecisionRecord",
    "KernelRole",
    "KernelSource",
    "KernelStep",
    "PipelineStage",
    "ReferenceMaterial",
    "RegistryEntry",
    "RegistryEvent",
    "RepairApplicationResult",
    "RepairDecision",
    "RepairOperation",
    "RepairPlan",
    "ReviewDimension",
    "RuntimeCheckRecord",
    "SCENARIO_KERNEL_DRAFT_VERSION",
    "SCENARIO_KERNEL_VERSION",
    "SCENARIO_REGISTRY_EVENT_VERSION",
    "SCENARIO_REGISTRY_VERSION",
    "SCENARIO_TASK_VERSION",
    "ScenarioKernel",
    "ScenarioKernelDraft",
    "ScenarioRegistry",
    "ScenarioTask",
    "SemanticReviewRecord",
    "StepBehaviorOracle",
    "TaskOrigin",
    "TaskProvenance",
    "TERMINAL_STAGES",
    "ToolCallCriterion",
    "seal_compiled_case",
    "seal_effect_spec",
    "seal_kernel",
    "seal_task",
    "stable_json",
    "upgrade_effect_spec_payload",
    "upgrade_kernel_payload",
    "validate_transition",
    "verify_compiled_case_hash",
    "verify_effect_kernel_binding",
    "verify_effect_spec_hash",
    "verify_kernel_hash",
    "verify_task_hash",
]
