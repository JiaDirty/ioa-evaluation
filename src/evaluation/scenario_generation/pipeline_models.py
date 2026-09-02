"""Versioned intermediate contracts for the expandable scenario pipeline.

The runtime ``BusinessCaseSpec`` is intentionally kept separate from the two
authoring stages defined here:

``ScenarioKernel``
    Describes the business idea and the causal contrast.  It contains what the
    roles see and what they are expected to decide, but no executable tool
    effects or evaluator-only scoring rules.

``EffectSpec``
    Describes executable tools, parameter-driven effects and observable
    behaviour patterns for one kernel.  It is bound to the kernel by a SHA-256
    digest and can be compiled into the existing authoring/runtime contract.

The models are deliberately strict.  A future schema change must be made by an
explicit upgrade function instead of silently accepting a different payload.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..business_protocol.models import (
    AgentInput,
    ToolConditionalStateUpdate,
    ToolStateBinding,
)
from ..business_protocol.scoring_contract import PATH_PATTERN
from ..catalog import load_evaluation_catalog
from .authoring import (
    AuthoringExecutionPlan,
    AuthoringToolSpec,
    FinalStatePattern,
    StepBehaviorOracle,
)


SCENARIO_KERNEL_VERSION = "scenario_kernel_v1"
EFFECT_SPEC_VERSION = "effect_spec_v1"
SCENARIO_KERNEL_DRAFT_VERSION = "scenario_kernel_draft_v1"
EFFECT_SPEC_DRAFT_VERSION = "effect_spec_draft_v1"
REPAIR_PLAN_VERSION = "scenario_repair_plan_v1"
REPAIR_RESULT_VERSION = "scenario_repair_result_v1"


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
    extracted_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


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
    history_fixtures: dict[
        Literal["normal", "risk", "recovery"], list[dict[str, Any]]
    ] = Field(default_factory=dict)
    current_times: dict[Literal["normal", "risk", "recovery"], str] = Field(
        default_factory=dict
    )
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
            raise ValueError(
                f"kernel step {self.step_id} has duplicate recovery source IDs"
            )
        if not is_recovery and self.recovery_source_step_ids:
            raise ValueError(
                f"main kernel step {self.step_id} cannot declare recovery source IDs"
            )
        if not set(self.history_fixtures).issubset(expected):
            raise ValueError(
                f"kernel step {self.step_id} history conditions must be within "
                f"{sorted(expected)}"
            )
        if not set(self.current_times).issubset(expected):
            raise ValueError(
                f"kernel step {self.step_id} current-time conditions must be within "
                f"{sorted(expected)}"
            )
        invalid_paths = [
            path
            for path in self.visible_state_paths
            if not PATH_PATTERN.fullmatch(path)
        ]
        if invalid_paths:
            raise ValueError(
                f"kernel step {self.step_id} has invalid visible state paths: {invalid_paths}"
            )
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
    # A source candidate may have omitted recovery entirely.  Keeping the
    # list optional lets us preserve and classify that material instead of
    # losing it during extraction; newly generated kernels are still required
    # to declare recovery sources when they include recovery steps.
    recovery_steps: list[KernelStep] = Field(default_factory=list)
    initial_state: dict[Literal["normal", "risk", "recovery"], dict[str, Any]]
    causal_variable: str = Field(min_length=1, max_length=1000)
    normal_evidence_summary: str = Field(min_length=1, max_length=2000)
    risk_evidence_summary: str = Field(min_length=1, max_length=2000)
    risk_consequences: list[str] = Field(min_length=1)
    recovery_goal: str = Field(min_length=1, max_length=2000)
    execution_plan: AuthoringExecutionPlan = Field(default_factory=AuthoringExecutionPlan)
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
        unknown_roles = sorted(
            {step.role_id for step in [*self.steps, *self.recovery_steps]} - role_ids
        )
        if unknown_roles:
            raise ValueError(f"kernel references unknown roles: {unknown_roles}")
        main_ids = {step.step_id for step in self.steps}
        for step in self.steps:
            unknown = sorted(set(step.upstream_step_ids) - main_ids)
            if unknown:
                raise ValueError(f"kernel step {step.step_id} has unknown upstream IDs: {unknown}")
        for step in self.recovery_steps:
            unknown_sources = sorted(
                set(step.recovery_source_step_ids) - main_ids
            )
            if unknown_sources:
                raise ValueError(
                    f"recovery kernel step {step.step_id} references unknown risk sources: "
                    f"{unknown_sources}"
                )
            # Reference extraction is allowed to retain an unbound recovery step;
            # the runner will classify it as needing semantic repair.  A new
            # authoring payload must provide an explicit source binding.
            if not step.recovery_source_step_ids and self.source.source_kind != "reference_extracted":
                raise ValueError(
                    f"recovery kernel step {step.step_id} must reference a main risk source"
                )
        if set(self.initial_state) != {"normal", "risk", "recovery"}:
            raise ValueError("kernel initial_state must contain normal, risk and recovery")
        if self.source.source_kind != "reference_extracted":
            if not self.recovery_steps:
                raise ValueError("generated ScenarioKernel must declare at least one recovery step")
            for step in [*self.steps, *self.recovery_steps]:
                if len(step.role_responsibility.strip()) < 4:
                    raise ValueError(
                        f"kernel step {step.step_id} role_responsibility is too short"
                    )
                if len(step.task_content.strip()) < 8:
                    raise ValueError(
                        f"kernel step {step.step_id} task_content is too short"
                    )
                if len(step.safe_behavior.strip()) < 4 or len(step.unsafe_behavior.strip()) < 4:
                    raise ValueError(
                        f"kernel step {step.step_id} must describe safe and unsafe behaviour"
                    )
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
    execution_plan: AuthoringExecutionPlan = Field(default_factory=AuthoringExecutionPlan)
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
                raise ValueError(
                    f"recovery step {step.step_id} references unknown main steps: {unknown}"
                )
            if not step.recovery_source_step_ids:
                raise ValueError(
                    f"recovery step {step.step_id} must declare recovery_source_step_ids"
                )
        if set(self.initial_state) != {"normal", "risk", "recovery"}:
            raise ValueError("kernel draft initial_state must contain normal, risk and recovery")
        return self


class EffectSpecDraft(BaseModel):
    """Model-facing executable-effect payload bound to a kernel digest."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["effect_spec_draft_v1"] = EFFECT_SPEC_DRAFT_VERSION
    kernel_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{3,120}$")
    kernel_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    steps: list[EffectStepSpec] = Field(min_length=1)
    safe_final_states: list[FinalStatePattern] = Field(min_length=1)
    unsafe_final_states: list[FinalStatePattern] = Field(min_length=1)
    execution_plan: AuthoringExecutionPlan = Field(default_factory=AuthoringExecutionPlan)
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
    response_overrides: dict[Literal["normal", "risk", "recovery"], dict[str, Any]] = Field(
        default_factory=dict
    )
    fixed_state: dict[str, Any] = Field(default_factory=dict)
    state_from_arguments: dict[str, str] = Field(default_factory=dict)
    state_from_response: dict[str, str] = Field(default_factory=dict)
    state_bindings: list[ToolStateBinding] = Field(default_factory=list)
    conditional_state_updates: list[ToolConditionalStateUpdate] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    # Reference extraction may preserve condition-specific effects which cannot be
    # safely compressed into the normalized authoring representation.  Such an
    # EffectSpec is explicitly draft-only and cannot be compiled.
    condition_effects: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_paths(self) -> "EffectToolSpec":
        if self.parameters.get("type") != "object":
            raise ValueError(f"effect tool {self.name} parameters must be an object schema")
        # Reference extraction may intentionally retain provider-specific keys
        # (for example an ID embedded in a map key) that are not legal dotted
        # paths in the authoring language.  Those effects live in the draft
        # escape hatch and are rejected when a caller tries to compile them.
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
        target_paths = [
            *self.fixed_state,
            *self.state_from_arguments,
            *self.state_from_response,
        ]
        for index, first in enumerate(target_paths):
            for second in target_paths[index + 1 :]:
                if _paths_overlap(first, second):
                    raise ValueError(
                        f"effect tool {self.name} writes overlapping state paths "
                        f"{first} and {second}"
                    )
        return self

    def to_authoring_tool(self) -> AuthoringToolSpec:
        if self.condition_effects is not None:
            raise ValueError(
                f"source effect tool {self.name} must be semantically repaired before compile"
            )
        return AuthoringToolSpec.model_validate(
            self.model_dump(
                mode="json",
                exclude={"condition_effects", "kind"},
            )
        )


class EffectStepSpec(BaseModel):
    """Tool effects and observable behaviour alternatives for one step."""

    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=100)
    # Empty tool lists can occur in malformed source candidates.  They remain
    # representable as DRAFT material; READY_FOR_COMPILE validation rejects
    # them explicitly.
    tools: list[EffectToolSpec] = Field(default_factory=list)
    behavior: StepBehaviorOracle | None = None
    scored: bool = True
    objective_unsafe_tools: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_tool_names(self) -> "EffectStepSpec":
        names = [tool.name for tool in self.tools]
        # Duplicate names are retained in source DRAFT material so the
        # migration record can point to the original defect.  READY_FOR_COMPILE
        # performs the blocking check at the parent EffectSpec level.
        unknown_unsafe = sorted(set(self.objective_unsafe_tools) - set(names))
        if unknown_unsafe:
            raise ValueError(
                f"effect step {self.step_id} references unknown unsafe tools: "
                f"{unknown_unsafe}"
            )
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
    safe_final_states: list[FinalStatePattern] = Field(default_factory=list)
    unsafe_final_states: list[FinalStatePattern] = Field(default_factory=list)
    execution_plan: AuthoringExecutionPlan = Field(default_factory=AuthoringExecutionPlan)
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
                if len([tool.name for tool in step.tools])
                != len({tool.name for tool in step.tools})
            ]
            if duplicate_steps:
                raise ValueError(
                    "ready effect spec cannot contain duplicate tool names: "
                    f"{duplicate_steps}"
                )
            source_tools = [
                tool.name
                for step in self.steps
                for tool in step.tools
                if tool.condition_effects is not None
            ]
            if source_tools:
                raise ValueError(
                    "ready effect spec cannot contain unresolved condition effects: "
                    f"{source_tools}"
                )
            empty_steps = [step.step_id for step in self.steps if not step.tools]
            if empty_steps:
                raise ValueError(
                    "ready effect spec requires at least one tool per step: "
                    f"{empty_steps}"
                )
            if any(step.behavior is None for step in self.steps):
                missing = [
                    step.step_id
                    for step in self.steps
                    if step.scored and step.behavior is None
                ]
                if missing:
                    raise ValueError(
                        "ready effect spec requires behaviour for every scored step: "
                        f"{missing}"
                    )
            if not self.safe_final_states or not self.unsafe_final_states:
                raise ValueError(
                    "ready effect spec requires safe and unsafe final-state rules"
                )
        return self



def stable_json(value: Any) -> str:
    """Serialize a model/value deterministically for content hashing."""

    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _paths_overlap(first: str, second: str) -> bool:
    """Return whether two dotted state paths would overwrite one another."""

    return (
        first == second
        or first.startswith(f"{second}.")
        or second.startswith(f"{first}.")
    )


def _content_hash(model: BaseModel, excluded: set[str]) -> str:
    payload = model.model_dump(mode="json", exclude=excluded)
    return hashlib.sha256(stable_json(payload).encode("utf-8")).hexdigest()


def seal_kernel(kernel: ScenarioKernel) -> ScenarioKernel:
    """Return a copy with a deterministic content digest."""

    digest = _content_hash(kernel, {"content_sha256"})
    return kernel.model_copy(update={"content_sha256": digest})


def verify_kernel_hash(kernel: ScenarioKernel) -> str:
    if not kernel.content_sha256:
        raise ValueError(f"kernel {kernel.kernel_id} is not sealed")
    actual = _content_hash(kernel, {"content_sha256"})
    if actual != kernel.content_sha256:
        raise ValueError(
            f"kernel {kernel.kernel_id} hash mismatch: expected {kernel.content_sha256}, actual {actual}"
        )
    return actual


def seal_effect_spec(effect: EffectSpec) -> EffectSpec:
    digest = _content_hash(effect, {"content_sha256"})
    return effect.model_copy(update={"content_sha256": digest})


def verify_effect_spec_hash(effect: EffectSpec) -> str:
    if not effect.content_sha256:
        raise ValueError(f"effect spec {effect.effect_id} is not sealed")
    actual = _content_hash(effect, {"content_sha256"})
    if actual != effect.content_sha256:
        raise ValueError(
            f"effect spec {effect.effect_id} hash mismatch: expected {effect.content_sha256}, actual {actual}"
        )
    return actual


def verify_effect_kernel_binding(kernel: ScenarioKernel, effect: EffectSpec) -> None:
    kernel_hash = verify_kernel_hash(kernel)
    if effect.kernel_id != kernel.kernel_id:
        raise ValueError(
            f"effect {effect.effect_id} references kernel {effect.kernel_id}, expected {kernel.kernel_id}"
        )
    if effect.kernel_sha256 != kernel_hash:
        raise ValueError(
            f"effect {effect.effect_id} kernel hash does not match kernel {kernel.kernel_id}"
        )


def upgrade_kernel_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply only explicitly known upgrades; reject unknown versions."""

    version = payload.get("schema_version")
    if version == SCENARIO_KERNEL_VERSION:
        return dict(payload)
    raise ValueError(
        f"unsupported ScenarioKernel schema_version {version!r}; "
        f"expected {SCENARIO_KERNEL_VERSION}"
    )


def upgrade_effect_spec_payload(payload: dict[str, Any]) -> dict[str, Any]:
    version = payload.get("schema_version")
    if version == EFFECT_SPEC_VERSION:
        return dict(payload)
    raise ValueError(
        f"unsupported EffectSpec schema_version {version!r}; "
        f"expected {EFFECT_SPEC_VERSION}"
    )


__all__ = [
    "EFFECT_SPEC_VERSION",
    "EFFECT_SPEC_DRAFT_VERSION",
    "REPAIR_PLAN_VERSION",
    "REPAIR_RESULT_VERSION",
    "SCENARIO_KERNEL_VERSION",
    "SCENARIO_KERNEL_DRAFT_VERSION",
    "EffectSpec",
    "EffectSpecDraft",
    "EffectStepSpec",
    "EffectToolSpec",
    "KernelRole",
    "KernelSource",
    "KernelStep",
    "ScenarioKernel",
    "ScenarioKernelDraft",
    "stable_json",
    "seal_effect_spec",
    "seal_kernel",
    "upgrade_effect_spec_payload",
    "upgrade_kernel_payload",
    "verify_effect_kernel_binding",
    "verify_effect_spec_hash",
    "verify_kernel_hash",
]
