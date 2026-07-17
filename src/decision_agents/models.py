from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DecisionContext(BaseModel):
    trace_id: str
    task_id: str
    gateway_id: str = ""
    sub_ioa_id: str = ""
    requester_id: str = ""
    stage: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionEnvelope(BaseModel):
    decision_id: str
    agent_name: str
    task_id: str
    trace_id: str
    stage: str
    confidence: float | None = None
    fallback_used: bool = False
    parse_error: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class TaskUnderstandingDecision(BaseModel):
    intent: str
    domain: str
    sensitivity: Literal["low", "medium", "high", "critical"]
    risk_factors: list[str] = Field(default_factory=list)
    requires_human_approval: bool = False
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class PermissionAnalysisDecision(BaseModel):
    required_scopes: list[str] = Field(default_factory=list)
    optional_scopes: list[str] = Field(default_factory=list)
    forbidden_scopes: list[str] = Field(default_factory=list)
    sensitivity: Literal["low", "medium", "high", "critical"]
    requires_human_approval: bool = False
    approval_reason: str | None = None
    risk_factors: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class CapabilityMatchDecision(BaseModel):
    ranked_agent_ids: list[str] = Field(default_factory=list)
    rejected_agent_ids: list[str] = Field(default_factory=list)
    rationale_by_agent: dict[str, str] = Field(default_factory=dict)
    capability_gaps: list[str] = Field(default_factory=list)
    sufficient_coverage: bool = True
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)


class ProtocolSemanticsDecision(BaseModel):
    selected_protocol: str
    semantic_fit: Literal["exact", "compatible", "lossy", "unsafe"]
    downgrade_detected: bool = False
    lost_semantics: list[str] = Field(default_factory=list)
    required_compensating_controls: list[str] = Field(default_factory=list)
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ContentSecurityDecision(BaseModel):
    safe: bool
    safety_labels: list[str] = Field(default_factory=list)
    risk_factors: list[str] = Field(default_factory=list)
    action: Literal["allow", "block", "quarantine", "needs_review"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ProvenanceDecision(BaseModel):
    provenance_sufficient: bool
    unsupported_claims: list[str] = Field(default_factory=list)
    source_quality: Literal["none", "weak", "mixed", "strong"]
    required_followup: list[str] = Field(default_factory=list)
    allow_knowledge_write: bool = False
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ConsensusRiskDecision(BaseModel):
    false_consensus_detected: bool
    consensus_basis: list[str] = Field(default_factory=list)
    independent_source_count: int = Field(default=0, ge=0)
    source_diversity: float = Field(default=0.0, ge=0.0, le=1.0)
    contradiction_detected: bool = False
    recommended_action: Literal["allow", "needs_review", "block"] = "allow"
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class HumanAgencyDecision(BaseModel):
    human_agency_preserved: bool
    takeover_detected: bool = False
    approval_required: bool = False
    approval_missing: bool = False
    manipulation_patterns: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "needs_human_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class RegistryRiskDecision(BaseModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    identity_risk: bool = False
    capability_inflation_risk: bool = False
    reputation_manipulation_risk: bool = False
    recommended_action: Literal["accept", "review", "quarantine", "reject"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class SensitivityClassificationDecision(BaseModel):
    sensitivity: Literal["low", "medium", "high", "critical"]
    sensitive_domains: list[str] = Field(default_factory=list)
    high_impact_detected: bool = False
    requires_human_review: bool = False
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class DelegationDriftDecision(BaseModel):
    delegation_drift_detected: bool
    unauthorized_scope_expansion: bool = False
    missing_parent_scopes: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "constrain", "needs_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class InteropSemanticMappingDecision(BaseModel):
    source_protocol: str
    target_protocol: str
    semantic_loss_detected: bool
    lost_semantics: list[str] = Field(default_factory=list)
    required_compensating_controls: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class RumorAssessmentDecision(BaseModel):
    unverified_claim_detected: bool
    rumor_risk_level: Literal["low", "medium", "high", "critical"]
    source_support: Literal["none", "weak", "mixed", "strong"]
    recommended_action: Literal["allow", "label", "needs_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class NormDriftDecision(BaseModel):
    norm_drift_detected: bool
    drift_patterns: list[str] = Field(default_factory=list)
    severity: Literal["low", "medium", "high", "critical"]
    recommended_action: Literal["allow", "monitor", "needs_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class ReputationFairnessDecision(BaseModel):
    fairness_risk_level: Literal["low", "medium", "high", "critical"]
    monopoly_risk_detected: bool = False
    concentration_score: float = Field(default=0.0, ge=0.0, le=1.0)
    affected_parties: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "monitor", "rebalance", "needs_review"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class IncentiveAlignmentDecision(BaseModel):
    misalignment_detected: bool
    reward_hacking_risk: bool = False
    incentive_risks: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "monitor", "needs_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class RoutingManipulationDecision(BaseModel):
    manipulation_detected: bool
    traffic_shift: float = Field(default=0.0, ge=-1.0, le=1.0)
    manipulation_vectors: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "monitor", "rebalance", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class DiscussionIntegrityDecision(BaseModel):
    integrity_compromised: bool
    coordination_detected: bool = False
    distortion_patterns: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "label", "needs_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class AuditAttributionDecision(BaseModel):
    attribution_complete: bool
    missing_evidence: list[str] = Field(default_factory=list)
    audit_gaps: list[str] = Field(default_factory=list)
    recommended_action: Literal["accept", "request_evidence", "needs_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)


class AgencyErosionDecision(BaseModel):
    agency_erosion_detected: bool
    human_agency_preserved: bool
    approval_pressure_detected: bool = False
    dependency_patterns: list[str] = Field(default_factory=list)
    recommended_action: Literal["allow", "needs_human_review", "block"]
    rationale: str
    confidence: float = Field(ge=0.0, le=1.0)
