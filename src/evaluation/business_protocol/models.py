"""Data contracts for the neutral business evaluation protocol."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


Condition = Literal["baseline", "mechanism", "recovery"]
RunLevel = Literal["key_node", "full_chain"]
Validity = Literal["VALID", "INVALID"]
SafetyOutcome = Literal["SAFE", "UNSAFE", "INDETERMINATE", "NOT_APPLICABLE"]


class BusinessRecord(BaseModel):
    record_id: str
    record_type: str
    created_at: str
    content: str
    source_refs: list[str] = Field(default_factory=list)


class UpstreamArtifact(BaseModel):
    artifact_id: str
    producer_role_id: str
    created_at: str
    content: str
    evidence_refs: list[str] = Field(default_factory=list)


class AgentInput(BaseModel):
    records: list[BusinessRecord] = Field(default_factory=list)
    upstream_artifacts: list[UpstreamArtifact] = Field(default_factory=list)


class BusinessToolSpec(BaseModel):
    """One provider-native tool plus deterministic per-condition fixtures."""

    name: str
    description: str
    parameters: dict[str, Any]
    responses: dict[Condition, dict[str, Any]] = Field(default_factory=dict)
    state_updates: dict[Condition, dict[str, Any]] = Field(default_factory=dict)
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
        return self

    def is_available(self, condition: Condition) -> bool:
        return condition in self.available_conditions


class ReferenceBehavior(BaseModel):
    """Evaluator-only examples; never included in model-visible input."""

    safe_summary: str
    unsafe_summary: str
    objective_unsafe_tools: list[str] = Field(default_factory=list)


class AgentStepSpec(BaseModel):
    step_id: str
    upstream_step_ids: list[str] = Field(default_factory=list)
    role_id: str
    role_responsibility: str
    task_id: str
    task_content: str
    current_time: str
    current_times: dict[Condition, str] = Field(default_factory=dict)
    business_object: str
    inputs: dict[Condition, AgentInput]
    raw_user_message: str | None = None
    history_fixtures: dict[Condition, list[dict[str, Any]]] = Field(default_factory=dict)
    tools: list[BusinessToolSpec] = Field(default_factory=list)
    reference: ReferenceBehavior | None = None
    key_node_fixture: bool = True
    key_node_target: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

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


class BusinessCaseSpec(BaseModel):
    case_id: str
    category: Literal["CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE"]
    title: str
    purpose: str
    steps: list[AgentStepSpec]
    recovery_steps: list[AgentStepSpec] = Field(default_factory=list)
    initial_state: dict[Condition, dict[str, Any]] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


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
