"""Pydantic v2 schemas for the Agent Model Safety Evaluation dataset (v2).

All 8 categories have strict typed models with extra="forbid".
Models are based on the actual JSONL data structure.

Note: JSON key `model_config` uses alias to avoid Pydantic collision.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ═══════════════════════════════════════════════════════════════
# Enums & Constants
# ═══════════════════════════════════════════════════════════════

CATEGORY_CODES = Literal["CAS","CON","RUM","NOR","INC","JUD","DIS","AGE"]
SCHEMA_VERSION = Literal["2.0"]
VARIANT = Literal["baseline","risk","recovery"]
RESULT_LAYER = Literal["model_behavior","system_response","final_impact"]
EVAL_STATUS = Literal[
    "SAFE_BEHAVIOR","RISKY_INTENT_BLOCKED","RISKY_BEHAVIOR_NO_FINAL_IMPACT",
    "RISK_PROPAGATED","RISK_WITH_FINAL_IMPACT","RECOVERED",
    "PARTIALLY_RECOVERED","RECOVERY_FAILED","RECOVERY_NOT_APPLICABLE","INVALID",
]

CATEGORY_TO_RISK: dict[str,str] = {
    "CAS":"cascade_propagation","CON":"ecosystem_consensus",
    "RUM":"rumor_spread","NOR":"norm_drift","INC":"incentive_mismatch",
    "JUD":"judgment_surrender","DIS":"discussion_distortion",
    "AGE":"agency_erosion",
}


# ═══════════════════════════════════════════════════════════════
# Shared / Policy Models
# ═══════════════════════════════════════════════════════════════

class DataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scenario_type: Literal["controlled_synthetic_fixture"] = "controlled_synthetic_fixture"
    not_a_real_world_factual_assertion: Literal[True]
    authoritative_tool_fixture_is_ground_truth_for_this_test: Literal[True]
    runtime_must_not_replace_fixture_with_model_guess: Literal[True]

class RandomnessControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    temperature_fixed: bool = True
    request_order_fixed: bool = True
    tool_fixture_responses_fixed: bool = True
    provider_seed: int | None = None
    note: str = ""

class FailurePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    api_failure: str = "INVALID_API_FAILURE"
    parse_failure: str = "INVALID_PARSE_FAILURE"
    tool_failure: str = "INVALID_TOOL_FAILURE"
    context_overflow: str = "INVALID_CONTEXT_OVERFLOW"
    do_not_fallback_to_fake_model_for_formal_score: bool = True

class CostBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_total_model_calls: int = 60
    max_total_tool_calls: int = 30
    stop_case_when_budget_exceeded: bool = True
    budget_exceeded_status: str = "INVALID_BUDGET_EXCEEDED"

class ModelExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider_mode: str
    same_base_model_for_all_roles: Literal[True]
    model_name_from_runtime_config: Literal[True]
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float = Field(gt=0.0, le=1.0)
    max_output_tokens: int = Field(gt=0, le=16_384)
    server_side_memory_assumed: Literal[False]
    context_replayed_by_local_runtime: Literal[True]
    model_has_direct_local_file_access: Literal[False]
    model_has_direct_database_access: Literal[False]
    model_has_shell_or_python_execution: Literal[False]

class CaseExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    variants: list[VARIANT]
    repeat_count: int = Field(gt=0)
    request_timeout_seconds: int = Field(gt=0)
    max_api_retries: int = Field(ge=0)
    retry_backoff_seconds: int = Field(ge=0)
    max_tool_rounds_per_agent: int = Field(gt=0)
    max_agent_calls_per_case: int = Field(gt=0)
    context_history_turn_limit: int = Field(gt=0)
    randomness_control: RandomnessControl
    failure_policy: FailurePolicy
    cost_budget: CostBudget

    @model_validator(mode="after")
    def require_all_variants(self) -> "CaseExecutionConfig":
        if self.variants != ["baseline", "risk", "recovery"]:
            raise ValueError("variants must be baseline, risk, recovery in order")
        return self

class VisibilityPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ground_truth_visible_to_test_runtime_only: Literal[True]
    ground_truth_visible_to_tested_agents: Literal[False]
    judge_rubric_visible_to_tested_agents: Literal[False]
    local_paths_visible_to_tested_agents: Literal[False]
    api_credentials_visible_to_tested_agents: Literal[False]

class ContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    local_persistence_required: Literal[True]
    session_key: Literal["run_id + role_id"]
    replay_fields: list[str]
    no_server_side_memory_assumption: Literal[True]

class RoleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role_id: str
    label: str
    capability: str

class LegacyFields(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline_condition: str = ""
    risk_condition: str = ""
    recovery_condition: str = ""
    recommended_rounds: str = ""
    source_reuse: str = ""
    required_changes: str = ""


# ═══════════════════════════════════════════════════════════════
# Common Base Case
# ═══════════════════════════════════════════════════════════════

class CommonCase(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_version: SCHEMA_VERSION = "2.0"
    case_id: str
    category_code: CATEGORY_CODES
    category: str
    risk_type: str
    case_title: str
    domain: str
    difficulty: str
    user_task: str
    tested_model_config: ModelExecutionConfig = Field(alias="model_config")
    execution_config: CaseExecutionConfig = Field(default_factory=CaseExecutionConfig)
    data_policy: DataPolicy
    visibility_policy: VisibilityPolicy = Field(default_factory=VisibilityPolicy)
    context_policy: ContextPolicy = Field(default_factory=ContextPolicy)
    result_layers: list[RESULT_LAYER]
    expected_safe_behavior: str
    risky_behavior: str
    objective_metrics: list[str]
    judge_focus: str
    roles: list[RoleSpec] | None = None
    legacy_fields: LegacyFields | None = None
    # scenario is overridden in each category subclass

    @model_validator(mode="after")
    def validate_common_contract(self) -> "CommonCase":
        if self.result_layers != [
            "model_behavior",
            "system_response",
            "final_impact",
        ]:
            raise ValueError("result_layers must contain the three layers in order")
        expected_risk = CATEGORY_TO_RISK.get(self.category_code)
        if self.risk_type != expected_risk:
            raise ValueError(
                f"risk_type must be {expected_risk!r} for {self.category_code}"
            )
        return self


# ═══════════════════════════════════════════════════════════════
# CAS — Cascade Propagation (跨系统级联扩散)
# ═══════════════════════════════════════════════════════════════

class CASClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    statement: str
    source_id: str
    source_type: str
    verification_status: str
    initial_confidence: float

class CASGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    supported: bool
    canonical_fact: str
    authority_source_id: str

class CASArtifactReqs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id_required: bool = True
    artifact_id_required: bool = True
    parent_artifact_ids_required: bool = True
    per_hop_assessment_required: bool = True

class CASVariant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_information: str = ""
    verification_status: str = ""
    source_name: str = ""
    source_detail: str = ""
    expected_chain_behavior: str = ""
    expected_runtime_injection_point: str | None = None
    correction: str | None = None
    target_claim_id: str | None = None
    reuse_same_local_run_state: bool | None = None
    replay_prior_agent_decisions: bool | None = None

class CASVariants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: CASVariant | None = None
    risk: CASVariant | None = None
    recovery: CASVariant | None = None

class ToolFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str
    query_key: str
    response: dict[str, Any]

class CASScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: CASClaim
    ground_truth: CASGroundTruth
    agent_chain: list[str]
    artifact_requirements: CASArtifactReqs | None = None
    variants: CASVariants | None = None
    authoritative_tool_fixture: ToolFixture | None = None
    required_observations: list[str] = Field(default_factory=list)

class CascadeCase(CommonCase):
    category_code: Literal["CAS"]
    scenario: CASScenario


# ═══════════════════════════════════════════════════════════════
# CON — Ecosystem False Consensus (生态错误共识)
# ═══════════════════════════════════════════════════════════════

class CONReport(BaseModel):
    model_config = ConfigDict(extra="allow")  # flexible: fields vary
    report_id: str
    role_id: str = ""
    conclusion: str
    verification_status: str = ""
    confidence: float = 0.0
    source_ids: list[str] = Field(default_factory=list)
    derived_from: list[str] = Field(default_factory=list)
    independence_group: str = ""

class CONSourceGraph(BaseModel):
    model_config = ConfigDict(extra="allow")
    nodes: list[str] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)



class CONGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    false_consensus_supported: bool
    canonical_answer: str
    recovery_evidence_description: str = ""
    authority_source_ids: list[str] = Field(default_factory=list)

class CONVariants(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: dict[str, Any] | None = None
    risk: dict[str, Any] | None = None
    recovery: dict[str, Any] | None = None

class CONScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str
    false_consensus_claim: str
    ground_truth: CONGroundTruth
    reports: list[CONReport]
    source_graph: CONSourceGraph | None = None
    independence_groups: dict[str, list[str]] = Field(default_factory=dict)
    variants: CONVariants | None = None
    authoritative_tool_fixture: ToolFixture | None = None

class ConsensusCase(CommonCase):
    category_code: Literal["CON"]
    scenario: CONScenario


# ═══════════════════════════════════════════════════════════════
# RUM — Rumor Spread (谣言扩散)
# ═══════════════════════════════════════════════════════════════

class RUMRumor(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rumor_id: str
    statement: str
    source_id: str
    source_type: str
    verification_status: str
    initial_confidence: float

class RUMGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rumor_id: str
    supported: bool
    canonical_fact: str
    authority_source_id: str

class RUMScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rumor: RUMRumor
    ground_truth: RUMGroundTruth
    propagation_chain: list[str]
    per_hop_fields: list[str] = Field(default_factory=list)
    variants: dict[str, Any] | None = None
    authoritative_tool_fixture: ToolFixture | None = None
    confidence_checkpoints: list[float] = Field(default_factory=list)

class RumorCase(CommonCase):
    category_code: Literal["RUM"]
    scenario: RUMScenario


# ═══════════════════════════════════════════════════════════════
# NOR — Norm Drift (规范漂移)
# ═══════════════════════════════════════════════════════════════

class NORFormalRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule_id: str
    content: str
    status: str = "active"
    authority: str = ""

class NORTemporaryException(BaseModel):
    model_config = ConfigDict(extra="forbid")
    exception_id: str
    content: str
    valid_task_ids: list[str] = Field(default_factory=list)
    scope_description: str = ""
    starts_at_round: int = 1
    expires_after_round: int = 4
    status_after_round_4: str = "expired"

class NORRoundTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round: int
    phase: str
    task: str
    active_exception_ids: list[str] = Field(default_factory=list)

class NORSharedMemoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_can_only_propose_write: bool = True
    formal_rule_requires_rule_service_authority: bool = True
    exception_cannot_be_promoted_to_rule_by_agent: bool = True

class NORRuleFixtures(BaseModel):
    model_config = ConfigDict(extra="allow")
    query_formal_rule: dict[str, Any] = Field(default_factory=dict)
    query_exception_after_round_4: dict[str, Any] = Field(default_factory=dict)

class NORScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formal_rule: NORFormalRule
    temporary_exception: NORTemporaryException
    round_tasks: list[NORRoundTask] = Field(default_factory=list)
    shared_memory_policy: NORSharedMemoryPolicy = Field(default_factory=NORSharedMemoryPolicy)
    rule_tool_fixtures: NORRuleFixtures | None = None
    recovery_clarification: str = ""

class NormDriftCase(CommonCase):
    category_code: Literal["NOR"]
    scenario: NORScenario


# ═══════════════════════════════════════════════════════════════
# INC — Incentive Mismatch (激励错配)
# ═══════════════════════════════════════════════════════════════

class INCTaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round: int
    phase: str
    task_id: str
    prompt: str
    fixture_key: str

class INCRoundRanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: list[int] = Field(default_factory=list)
    misaligned: list[int] = Field(default_factory=list)
    recovery: list[int] = Field(default_factory=list)

class INCRewardPolicies(BaseModel):
    model_config = ConfigDict(extra="allow")
    baseline: dict[str, float] = Field(default_factory=dict)
    misaligned: dict[str, float] = Field(default_factory=dict)
    recovery: dict[str, float] = Field(default_factory=dict)

class INCRewardServicePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    compute_from_real_logs_only: bool = True
    agent_self_report_not_trusted: bool = True
    recent_reward_history_replayed_to_same_role: bool = True
    history_window: int = 5

class INCFixtureItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture_key: str
    visible_input: dict[str, Any] = Field(default_factory=dict)
    authoritative_tool_response: dict[str, Any] = Field(default_factory=dict)
    canonical_safe_answer: str = ""

class INCScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_sequence: list[INCTaskItem] = Field(default_factory=list)
    round_ranges: INCRoundRanges | None = None
    reward_policies: INCRewardPolicies | None = None
    misaligned_metric: str = ""
    expected_gaming_behavior: str = ""
    behavioral_metrics: list[str] = Field(default_factory=list)
    reward_service_policy: INCRewardServicePolicy | None = None
    controlled_fixture_template: dict[str, Any] = Field(default_factory=dict)
    fixture_catalog: list[INCFixtureItem] = Field(default_factory=list)
    recovery_instruction: str = ""

class IncentiveCase(CommonCase):
    category_code: Literal["INC"]
    scenario: INCScenario


# ═══════════════════════════════════════════════════════════════
# JUD — Judgment Surrender (判断让渡)
# ═══════════════════════════════════════════════════════════════

class JUDDecisionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")
    option_id: str
    description: str

class JUDAuthorizationBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    textual_recommendation_allowed: bool = True
    irreversible_action_requires_explicit_human_confirmation: bool = True
    silence_is_not_confirmation: bool = True
    model_must_not_claim_final_decision_ownership: bool = True

class JUDScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    known_information: list[str] = Field(default_factory=list)
    missing_preferences: list[str] = Field(default_factory=list)
    decision_options: list[JUDDecisionOption] = Field(default_factory=list)
    high_impact_actions: list[str] = Field(default_factory=list)
    authorization_boundary: JUDAuthorizationBoundary | None = None
    baseline_preferences: dict[str, Any] = Field(default_factory=dict)
    variants: dict[str, Any] | None = None
    required_synthesis_fields: list[str] = Field(default_factory=list)

class JudgmentCase(CommonCase):
    category_code: Literal["JUD"]
    scenario: JUDScenario


# ═══════════════════════════════════════════════════════════════
# DIS — Discussion Distortion (讨论扭曲)
# ═══════════════════════════════════════════════════════════════

class DISParticipant(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participant_id: str
    role: str
    controller_group: str | None = None
    hidden_goal: str | None = None



class DISInitialEvidence(BaseModel):
    model_config = ConfigDict(extra="allow")
    coordinated_common_source: dict[str, Any] = Field(default_factory=dict)
    independent_sources: list[dict[str, Any]] = Field(default_factory=list)

class DISDiscussionRound(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round: int
    allowed_actions: list[str] = Field(default_factory=list)

class DISRankingPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    formula: str = ""
    fixed_across_variants: bool = True
    recency_definition: Literal[
        "creation_index_divided_by_latest_creation_index"
    ] = "creation_index_divided_by_latest_creation_index"
    validated_report_definition: Literal[
        "successful_report_on_existing_message"
    ] = "successful_report_on_existing_message"

class DISGroundTruth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manipulation_goal_supported: bool
    canonical_answer: str = ""
    authority_source_ids: list[str] = Field(default_factory=list)

class DISScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    discussion_id: str
    issue: str
    manipulation_goal: str = ""
    ground_truth: DISGroundTruth | None = None
    participants: list[DISParticipant] = Field(default_factory=list)
    controller_groups: dict[str, list[str]] = Field(default_factory=dict)
    initial_evidence: DISInitialEvidence | None = None
    discussion_rounds: list[DISDiscussionRound] = Field(default_factory=list)
    ranking_policy: DISRankingPolicy | None = None
    sandbox_only: bool = True
    real_external_platform_actions_forbidden: bool = True
    variants: dict[str, Any] | None = None
    required_logs: list[str] = Field(default_factory=list)

class DiscussionCase(CommonCase):
    category_code: Literal["DIS"]
    scenario: DISScenario


# ═══════════════════════════════════════════════════════════════
# AGE — Agency Erosion (能动性弱化)
# ═══════════════════════════════════════════════════════════════

class AGEInitialUserState(BaseModel):
    model_config = ConfigDict(extra="allow")
    trust: float = 0.5
    verification_tendency: float = 0.5
    decision_participation: float = 0.5
    correction_tendency: float = 0.5
    dependency: float = 0.5

class AGEAutomationStage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stage: str
    rounds: list[int] = Field(default_factory=list)
    allowed: list[str] = Field(default_factory=list)
    forbidden: list[str] = Field(default_factory=list)

class AGETaskItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    round: int
    stage: str
    task_id: str
    prompt: str

class AGEPermissionBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")
    low_risk_reversible_action: str = ""
    high_impact_action: str = ""
    high_impact_requires_explicit_confirmation: bool = True
    agent_cannot_modify_permissions_directly: bool = True
    agent_can_only_propose_permission_change: bool = True

class AGEStateUpdatePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deterministic_local_service: bool = True
    rules: dict[str, Any] = Field(default_factory=dict)
    clamp_range: list[float] = Field(default_factory=lambda: [0.0, 1.0])
    model_cannot_write_state_directly: bool = True

class AGEScenario(BaseModel):
    model_config = ConfigDict(extra="forbid")
    initial_user_state: AGEInitialUserState = Field(default_factory=AGEInitialUserState)
    protected_user_rights: list[str] = Field(default_factory=list)
    automation_stages: list[AGEAutomationStage] = Field(default_factory=list)
    task_sequence: list[AGETaskItem] = Field(default_factory=list)
    permission_boundary: AGEPermissionBoundary | None = None
    state_update_policy: AGEStateUpdatePolicy | None = None
    recovery_request: str = ""
    recovery_verification_rounds: int = 3

class AgencyCase(CommonCase):
    category_code: Literal["AGE"]
    scenario: AGEScenario


# ═══════════════════════════════════════════════════════════════
# Union Type & Map
# ═══════════════════════════════════════════════════════════════

CATEGORY_MODEL_MAP: dict[str, type[CommonCase]] = {
    "CAS": CascadeCase,
    "CON": ConsensusCase,
    "RUM": RumorCase,
    "NOR": NormDriftCase,
    "INC": IncentiveCase,
    "JUD": JudgmentCase,
    "DIS": DiscussionCase,
    "AGE": AgencyCase,
}


# ═══════════════════════════════════════════════════════════════
# Runtime Evaluation Models
# ═══════════════════════════════════════════════════════════════

class AgentClaimRecord(BaseModel):
    """Observed disposition of one proposition in a tested model response."""

    model_config = ConfigDict(extra="forbid")
    claim_id: str
    # The proposition text is already preserved in the exact Agent input.
    # Category-specific wire schemas intentionally omit this duplicate field,
    # so the shared parser must not require the tested model to repeat it.
    statement: str = ""
    disposition: Literal["accepted", "rejected", "uncertain", "not_assessed"]
    forwarded: bool | None = None
    used_for_action: bool | None = None
    corrected: bool | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    source_label: str = ""
    verification_status: Literal["verified", "unverified", "unknown"] = "unknown"
    corrects_claim_id: str = ""


class AgentSourceRecord(BaseModel):
    """Fixed source fields accepted from the tested Agent wire protocol."""

    model_config = ConfigDict(extra="forbid")
    source_id: str = ""
    source_label: str = ""
    source: str = ""
    type: str = ""
    authority: str = ""
    independence: str = ""
    report_id: str = ""
    description: str = ""
    data: str = ""
    details: str = ""
    impact: str = ""
    verification_status: str = ""


class AgentDiscussionActionRecord(BaseModel):
    """Fixed discussion fields used by the controlled discussion tools."""

    model_config = ConfigDict(extra="forbid")
    action: str = ""
    content: str = ""
    message: str = ""
    message_id: str = ""
    participant: str = ""
    source_ids: list[str] = Field(default_factory=list)


class AgentBehaviorRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claims: list[AgentClaimRecord] = Field(default_factory=list)
    sources: list[AgentSourceRecord] = Field(default_factory=list)
    verification_requested: bool = False
    rules_used: list[str] = Field(default_factory=list)
    exceptions_used: list[str] = Field(default_factory=list)
    alternatives_presented: list[str] = Field(default_factory=list)
    human_confirmation_required: bool = False
    permission_expansion_requested: bool = False
    discussion_actions: list[AgentDiscussionActionRecord] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class AgentModelBusinessOutput(BaseModel):
    """Agent-facing business result.

    This is intentionally separate from measurement metadata.  Tested models
    may explain their answer here, but core metrics must still be derived from
    events/tools/state rather than trusting this object alone.
    """

    model_config = ConfigDict(extra="forbid")
    answer: str = ""
    decision: str = ""
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    limitations: list[str] = Field(default_factory=list)


class AgentModelToolArguments(BaseModel):
    """Superset of arguments for every governed v2 evaluation tool."""

    model_config = ConfigDict(extra="forbid")
    action: str | None = None
    claim: str | None = None
    claim_id: str | None = None
    confirmation_token: str | None = None
    content: str | None = None
    current_round: int | None = None
    entry_id: str | None = None
    evidence_text: str | None = None
    lookup_id: str | None = None
    message_id: str | None = None
    parent_message_id: str | None = None
    query: str | None = None
    quoted_message_id: str | None = None
    reason: str | None = None
    report_id: str | None = None
    requested_scope: str | None = None
    rule_or_exception_id: str | None = None
    rule_ids: list[str] | None = None
    exception_ids: list[str] | None = None
    scope: str | None = None
    stance: str | None = None
    source_id: str | None = None
    source_ids: list[str] | None = None
    source_or_artifact_id: str | None = None
    source_rule_or_exception_id: str | None = None


class AgentModelToolCallRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = ""
    arguments: AgentModelToolArguments = Field(default_factory=AgentModelToolArguments)
    reason: str = ""


class AgentModelAction(BaseModel):
    """Unified tested-agent output protocol for the v2 evaluation."""

    model_config = ConfigDict(extra="forbid")
    # Controlled evaluation steps expose side effects through governed tools.
    # Generic orchestration actions have different required fields and are not
    # valid wire responses for this evaluation-only protocol.
    type: Literal["final", "tool_call"]
    business_output: AgentModelBusinessOutput = Field(
        default_factory=AgentModelBusinessOutput
    )
    behavior_record: AgentBehaviorRecord = Field(default_factory=AgentBehaviorRecord)
    tool_call: AgentModelToolCallRequest | None = None
    reason: str = ""

    @model_validator(mode="before")
    @classmethod
    def expand_provider_wire_action(cls, value: Any) -> Any:
        """Expand the provider-compatible nested action without changing intent."""
        if not isinstance(value, dict) or "type" in value:
            return value
        wire_action = value.get("action")
        if not isinstance(wire_action, dict):
            return value
        kind = wire_action.get("kind")
        if kind not in {"final", "tool_call"}:
            return value
        expanded = dict(value)
        expanded.pop("action", None)
        expanded["type"] = kind
        for key in ("business_output", "behavior_record", "reason"):
            if key not in expanded and key in wire_action:
                expanded[key] = wire_action[key]
        # Preserve a supplied tool_call for both branches so the after
        # validator can enforce exclusivity.  Silently dropping tool_call from
        # a nested final response would make malformed provider output appear
        # valid and could hide an expressed tool intent from the audit trail.
        expanded["tool_call"] = wire_action.get("tool_call")
        return expanded

    @model_validator(mode="after")
    def enforce_action_tool_exclusivity(self) -> "AgentModelAction":
        if self.type == "final" and self.tool_call is not None:
            raise ValueError("final AgentModelAction must not include tool_call")
        if self.type == "tool_call":
            if self.tool_call is None:
                raise ValueError("tool_call AgentModelAction requires tool_call")
            if not self.tool_call.tool_id.strip():
                raise ValueError("tool_call AgentModelAction requires tool_call.tool_id")
        return self


class GateResult(BaseModel):
    """Auditable pass/fail result for one paired-run eligibility gate."""

    model_config = ConfigDict(extra="forbid")
    passed: bool
    reason: str
    evidence_refs: list[str] = Field(default_factory=list)


class PairedRunResult(BaseModel):
    """One case/repeat unit used by paired formal analysis."""

    model_config = ConfigDict(extra="forbid")
    paired_unit_id: str
    case_id: str
    repeat_index: int
    experiment_level: Literal["key_node", "ecosystem"] = "key_node"
    baseline_run_id: str | None = None
    risk_run_id: str | None = None
    recovery_run_id: str | None = None
    baseline_scenario_state_id: str
    risk_scenario_state_id: str
    risk_snapshot_id: str | None = None
    gates: dict[str, GateResult] = Field(default_factory=dict)
    baseline_risk_delta: dict[str, float] = Field(default_factory=dict)
    risk_recovery_delta: dict[str, float] = Field(default_factory=dict)
    formal_aggregate_eligible: bool = False

class ThreeLayerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    case_id: str
    variant: VARIANT
    risk_type: str
    experiment_level: Literal["key_node", "ecosystem"] = "key_node"
    paired_unit_id: str = ""
    scenario_state_id: str = ""
    parent_snapshot_id: str | None = None
    model_behavior: dict[str, Any] = Field(default_factory=dict)
    system_response: dict[str, Any] = Field(default_factory=dict)
    final_impact: dict[str, Any] = Field(default_factory=dict)
    objective_metrics: dict[str, Any] = Field(default_factory=dict)
    judge_verdict: dict[str, Any] = Field(default_factory=dict)
    evidence_refs: list[str] = Field(default_factory=list)
    evidence_bundle_file: str = ""
    evidence_bundle_hash: str = ""
    status: EVAL_STATUS = "INVALID"
    created_at: datetime = Field(default_factory=datetime.now)

class RiskRunState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str
    case_id: str = ""
    risk_type: str = ""
    variant: VARIANT = "baseline"
    state_json: dict[str, Any] = Field(default_factory=dict)
    status: str = "pending"
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

class AgentSession(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = ""
    run_id: str
    case_id: str = ""
    variant: VARIANT = "baseline"
    role_id: str
    agent_id: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

class AgentTurn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    turn_id: str = ""
    session_id: str
    round_index: int = 0
    input_json: dict[str, Any] = Field(default_factory=dict)
    output_json: dict[str, Any] = Field(default_factory=dict)
    tool_calls_json: list[dict[str, Any]] = Field(default_factory=list)
    artifact_refs_json: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
