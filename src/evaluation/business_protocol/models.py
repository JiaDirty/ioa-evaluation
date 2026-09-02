"""Data contracts for the neutral business evaluation protocol."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..scenario_generation.catalog import load_evaluation_catalog
from .scoring_contract import GenericScoringContract, PATH_PATTERN


Condition = Literal["baseline", "mechanism", "recovery"]
RunLevel = Literal["key_node", "full_chain"]
Validity = Literal["VALID", "INVALID"]
SafetyOutcome = Literal["SAFE", "UNSAFE", "INDETERMINATE", "NOT_APPLICABLE"]


class BusinessRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str
    record_type: str
    created_at: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class UpstreamArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    producer_role_id: str
    created_at: str
    content: str
    evidence_refs: list[str] = Field(default_factory=list)


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    records: list[BusinessRecord] = Field(default_factory=list)
    upstream_artifacts: list[UpstreamArtifact] = Field(default_factory=list)


class ToolStateBinding(BaseModel):
    """Copy an actual tool argument or response value into business state."""

    model_config = ConfigDict(extra="forbid")

    target_path: str
    source: Literal["argument", "response"]
    source_path: str
    conditions: list[Condition] = Field(
        default_factory=lambda: ["baseline", "mechanism", "recovery"]
    )

    @model_validator(mode="after")
    def validate_paths(self) -> "ToolStateBinding":
        if not PATH_PATTERN.fullmatch(self.target_path):
            raise ValueError("target_path must be a dotted state path")
        if not PATH_PATTERN.fullmatch(self.source_path):
            raise ValueError("source_path must be a dotted data path")
        if not self.conditions or len(self.conditions) != len(set(self.conditions)):
            raise ValueError("binding conditions must be non-empty and unique")
        return self


class ToolConditionalStateUpdate(BaseModel):
    """Apply declared state only when a successful call matches arguments."""

    model_config = ConfigDict(extra="forbid")

    conditions: list[Condition] = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    argument_not_equals: dict[str, Any] = Field(default_factory=dict)
    state_updates: dict[str, Any]

    @model_validator(mode="after")
    def validate_shape(self) -> "ToolConditionalStateUpdate":
        if len(self.conditions) != len(set(self.conditions)):
            raise ValueError("conditional state update conditions must be unique")
        if not self.state_updates:
            raise ValueError("conditional state update must change business state")
        overlap = sorted(
            set(_leaf_paths(self.arguments)) & set(_leaf_paths(self.argument_not_equals))
        )
        if overlap:
            raise ValueError(
                f"conditional update cannot require equals and not-equals on {overlap}"
            )
        return self


class BusinessToolSpec(BaseModel):
    """One provider-native tool plus deterministic per-condition fixtures."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    parameters: dict[str, Any]
    responses: dict[Condition, dict[str, Any]] = Field(default_factory=dict)
    state_updates: dict[Condition, dict[str, Any]] = Field(default_factory=dict)
    state_bindings: list[ToolStateBinding] = Field(default_factory=list)
    conditional_state_updates: list[ToolConditionalStateUpdate] = Field(
        default_factory=list
    )
    available_conditions: list[Condition] = Field(
        default_factory=lambda: ["baseline", "mechanism", "recovery"]
    )
    risk_level: Literal["low", "medium", "high", "critical"] = "low"

    @model_validator(mode="after")
    def require_object_schema(self) -> "BusinessToolSpec":
        if self.parameters.get("type") != "object":
            raise ValueError(f"tool {self.name} parameters must be an object schema")
        if not self.available_conditions:
            raise ValueError(f"tool {self.name} must be available in at least one condition")
        if len(self.available_conditions) != len(set(self.available_conditions)):
            raise ValueError(f"tool {self.name} has duplicate available conditions")
        self.parameters.setdefault("additionalProperties", False)
        bindings_by_condition: dict[str, list[ToolStateBinding]] = {
            condition: [] for condition in self.available_conditions
        }
        for binding in self.state_bindings:
            if binding.source == "argument":
                if not _schema_has_path(self.parameters, binding.source_path):
                    raise ValueError(
                        f"tool {self.name} binding references unknown argument path "
                        f"{binding.source_path}"
                    )
            for condition in binding.conditions:
                if condition not in self.available_conditions:
                    raise ValueError(
                        f"tool {self.name} binding uses unavailable condition {condition}"
                    )
                if binding.source == "response" and not _value_has_path(
                    self.responses.get(condition, {}), binding.source_path
                ):
                    raise ValueError(
                        f"tool {self.name} binding references unknown response path "
                        f"{binding.source_path} in {condition}"
                    )
                update_paths = _leaf_paths(self.state_updates.get(condition, {}))
                if any(
                    _paths_overlap(binding.target_path, update_path)
                    for update_path in update_paths
                ):
                    raise ValueError(
                        f"tool {self.name} writes {binding.target_path} through both "
                        "state_updates and state_bindings"
                    )
                for existing in bindings_by_condition[condition]:
                    if _paths_overlap(binding.target_path, existing.target_path):
                        raise ValueError(
                            f"tool {self.name} has overlapping state bindings "
                            f"{existing.target_path} and {binding.target_path} in {condition}"
                        )
                bindings_by_condition[condition].append(binding)
        seen_effects: set[str] = set()
        for effect in self.conditional_state_updates:
            unavailable = sorted(set(effect.conditions) - set(self.available_conditions))
            if unavailable:
                raise ValueError(
                    f"tool {self.name} conditional update uses unavailable "
                    f"conditions: {unavailable}"
                )
            unknown_argument_paths = sorted(
                path
                for path in {
                    *_leaf_paths(effect.arguments),
                    *_leaf_paths(effect.argument_not_equals),
                }
                if not _schema_has_path(self.parameters, path)
            )
            if unknown_argument_paths:
                raise ValueError(
                    f"tool {self.name} conditional update references unknown "
                    f"argument paths: {unknown_argument_paths}"
                )
            signature = json.dumps(
                {
                    "conditions": sorted(effect.conditions),
                    "arguments": effect.arguments,
                    "argument_not_equals": effect.argument_not_equals,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if signature in seen_effects:
                raise ValueError(
                    f"tool {self.name} has duplicate conditional state updates"
                )
            seen_effects.add(signature)
        return self

    def is_available(self, condition: Condition) -> bool:
        return condition in self.available_conditions


def _schema_has_path(schema: dict[str, Any], path: str) -> bool:
    current = schema
    for segment in path.split("."):
        if segment.isdigit():
            current = current.get("items", {}) if isinstance(current, dict) else {}
            continue
        properties = current.get("properties", {}) if isinstance(current, dict) else {}
        if segment not in properties:
            return False
        current = properties[segment]
    return True


def _value_has_path(value: Any, path: str) -> bool:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            return False
    return True


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if isinstance(value, dict) and value:
        paths: set[str] = set()
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            paths.update(_leaf_paths(item, child))
        return paths
    return {prefix} if prefix else set()


def _paths_overlap(first: str, second: str) -> bool:
    return (
        first == second
        or first.startswith(f"{second}.")
        or second.startswith(f"{first}.")
    )


class ReferenceBehavior(BaseModel):
    """Evaluator-only examples; never included in model-visible input."""

    model_config = ConfigDict(extra="forbid")

    safe_summary: str
    unsafe_summary: str
    objective_unsafe_tools: list[str] = Field(default_factory=list)


class AgentStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str
    upstream_step_ids: list[str] = Field(default_factory=list)
    role_id: str
    role_responsibility: str
    task_id: str
    task_content: str
    current_time: str
    current_times: dict[Condition, str] = Field(default_factory=dict)
    business_object: str
    visible_state_paths: list[str] = Field(default_factory=list)
    inputs: dict[Condition, AgentInput]
    raw_user_message: str | None = None
    history_fixtures: dict[Condition, list[dict[str, Any]]] = Field(default_factory=dict)
    tools: list[BusinessToolSpec] = Field(default_factory=list)
    reference: ReferenceBehavior | None = None
    key_node_fixture: bool = True
    key_node_target: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_visible_state_paths(self) -> "AgentStepSpec":
        if len(self.visible_state_paths) != len(set(self.visible_state_paths)):
            raise ValueError(f"step {self.step_id} visible_state_paths must be unique")
        invalid = [
            path
            for path in self.visible_state_paths
            if not PATH_PATTERN.fullmatch(path)
            or path.split(".", 1)[0] in {"impact_flags", "key_node_states"}
        ]
        if invalid:
            raise ValueError(
                f"step {self.step_id} has invalid or evaluator-only visible state paths: "
                f"{invalid}"
            )
        return self

    def input_for(self, condition: Condition) -> AgentInput:
        if condition in self.inputs:
            return self.inputs[condition]
        if "mechanism" in self.inputs:
            return self.inputs["mechanism"]
        raise KeyError(f"step {self.step_id} has no input for {condition}")

    def history_for(self, condition: Condition) -> list[dict[str, Any]]:
        return deepcopy(self.history_fixtures.get(condition, []))

    def current_time_for(self, condition: Condition) -> str:
        return self.current_times.get(condition, self.current_time)

    def tools_for(self, condition: Condition) -> list[BusinessToolSpec]:
        return [tool for tool in self.tools if tool.is_available(condition)]


class ExecutionPlan(BaseModel):
    """Data-declared pairing and recovery policy for one scenario."""

    model_config = ConfigDict(extra="forbid")

    pairing: Literal["independent", "shared_prefix"] = "independent"
    shared_prefix_step_ids: list[str] = Field(default_factory=list)
    baseline_state_overrides: dict[str, Any] = Field(default_factory=dict)
    recovery_policy: Literal["on_mechanism_unsafe", "always", "never"] = (
        "on_mechanism_unsafe"
    )
    recovery_step_ids: list[str] | None = None

    @model_validator(mode="after")
    def validate_shape(self) -> "ExecutionPlan":
        if self.pairing == "independent" and self.shared_prefix_step_ids:
            raise ValueError(
                "shared_prefix_step_ids require pairing='shared_prefix'"
            )
        if self.pairing == "shared_prefix" and not self.shared_prefix_step_ids:
            raise ValueError(
                "pairing='shared_prefix' requires at least one shared prefix step"
            )
        if self.pairing == "independent" and self.baseline_state_overrides:
            raise ValueError(
                "baseline_state_overrides require pairing='shared_prefix'"
            )
        invalid_paths = [
            path for path in self.baseline_state_overrides
            if not PATH_PATTERN.fullmatch(path)
        ]
        if invalid_paths:
            raise ValueError(
                f"baseline_state_overrides must use dotted state paths: {invalid_paths}"
            )
        if len(self.shared_prefix_step_ids) != len(set(self.shared_prefix_step_ids)):
            raise ValueError("shared_prefix_step_ids must be unique")
        if self.recovery_step_ids is not None:
            if not self.recovery_step_ids:
                raise ValueError("recovery_step_ids cannot be empty when provided")
            if len(self.recovery_step_ids) != len(set(self.recovery_step_ids)):
                raise ValueError("recovery_step_ids must be unique")
        return self


class BusinessCaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    category: str
    title: str
    purpose: str
    steps: list[AgentStepSpec]
    recovery_steps: list[AgentStepSpec] = Field(default_factory=list)
    initial_state: dict[Condition, dict[str, Any]] = Field(default_factory=dict)
    scoring_contract: GenericScoringContract | None = None
    execution_plan: ExecutionPlan = Field(default_factory=ExecutionPlan)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("category", mode="before")
    @classmethod
    def normalize_category(cls, value: str) -> str:
        catalog = load_evaluation_catalog()
        if value in catalog.category_names_zh:
            return catalog.code_for_name_zh(value)
        if value not in catalog.category_codes:
            raise ValueError("unknown evaluation category")
        return value

    @model_validator(mode="after")
    def validate_scoring_contract(self) -> "BusinessCaseSpec":
        from .scoring_contract import validate_scoring_contract_for_case

        validate_scoring_contract_for_case(self)
        main_ids = [step.step_id for step in self.steps]
        prefix_ids = self.execution_plan.shared_prefix_step_ids
        if prefix_ids and main_ids[: len(prefix_ids)] != prefix_ids:
            raise ValueError(
                "execution plan shared_prefix_step_ids must be a contiguous prefix of steps"
            )
        if self.execution_plan.recovery_step_ids is not None:
            recovery_ids = {step.step_id for step in self.recovery_steps}
            unknown = sorted(set(self.execution_plan.recovery_step_ids) - recovery_ids)
            if unknown:
                raise ValueError(
                    f"execution plan references unknown recovery steps: {unknown}"
                )
        known_state_roots = {
            str(key)
            for condition_state in self.initial_state.values()
            for key in condition_state
        }
        for step in [*self.steps, *self.recovery_steps]:
            for tool in step.tools:
                known_state_roots.update(
                    str(key)
                    for update in tool.state_updates.values()
                    for key in update
                )
                known_state_roots.update(
                    binding.target_path.split(".", 1)[0]
                    for binding in tool.state_bindings
                )
        unknown_visible_roots = sorted(
            {
                path.split(".", 1)[0]
                for step in [*self.steps, *self.recovery_steps]
                for path in step.visible_state_paths
                if path.split(".", 1)[0] not in known_state_roots
            }
        )
        if unknown_visible_roots:
            raise ValueError(
                "steps expose unknown business state roots: "
                f"{unknown_visible_roots}"
            )
        unknown_override_roots = sorted(
            path.split(".", 1)[0]
            for path in self.execution_plan.baseline_state_overrides
            if path.split(".", 1)[0] not in known_state_roots
        )
        if unknown_override_roots:
            raise ValueError(
                "execution plan baseline overrides unknown state roots: "
                f"{sorted(set(unknown_override_roots))}"
            )
        return self


class AgentBusinessResult(BaseModel):
    status: Literal["COMPLETED", "PENDING", "NEEDS_INPUT", "BLOCKED"]
    decision: str = Field(min_length=1, max_length=160)
    answer: str = Field(min_length=1, max_length=400)
    evidence_refs: list[str]
    next_action: str = Field(max_length=200)
    handoff_message: str = Field(max_length=240)
    decision_basis: str = Field(
        min_length=1,
        max_length=600,
        description=(
            "简短说明本轮决定直接依据的记录或工具结果，以及这些证据与决定的关系；"
            "不要求也不表示提供隐藏思维过程。"
        ),
    )


class ToolIntentEvidence(BaseModel):
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]


class ToolResultEvidence(BaseModel):
    tool_call_id: str
    tool_name: str
    execution_status: Literal["SUCCEEDED", "FAILED", "DENIED"]
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class StepTrace(BaseModel):
    case_id: str
    category: str
    condition: Condition
    run_level: RunLevel
    step_id: str
    role_id: str
    outcome_relevant: bool = True
    request_snapshots: list[dict[str, Any]] = Field(default_factory=list)
    model_messages: list[dict[str, Any]] = Field(default_factory=list)
    visible_reasoning: list[dict[str, Any]] = Field(default_factory=list)
    provider_audit: list[dict[str, Any]] = Field(default_factory=list)
    model_decision: AgentBusinessResult | None = None
    tool_intents: list[ToolIntentEvidence] = Field(default_factory=list)
    tool_results: list[ToolResultEvidence] = Field(default_factory=list)
    state_before: dict[str, Any] = Field(default_factory=dict)
    state_after: dict[str, Any] = Field(default_factory=dict)
    final_result: AgentBusinessResult | None = None
    validity: Validity = "VALID"
    model_intent_outcome: SafetyOutcome | None = "INDETERMINATE"
    intent_score_reasons: list[str] = Field(default_factory=list)
    safety_outcome: SafetyOutcome | None = "INDETERMINATE"
    score_reasons: list[str] = Field(default_factory=list)
    error: str | None = None


class CaseRunResult(BaseModel):
    case_id: str
    category: str
    condition: Condition
    run_level: RunLevel
    traces: list[StepTrace]
    final_state: dict[str, Any]
    validity: Validity
    model_intent_outcome: SafetyOutcome | None
    safety_outcome: SafetyOutcome | None
    impact_outcome: SafetyOutcome | None


class PairedCaseRunResult(BaseModel):
    case_id: str
    category: str
    run_level: RunLevel
    baseline: CaseRunResult
    mechanism: CaseRunResult
    recovery: CaseRunResult
    shared_prefix_step_count: int = 0
