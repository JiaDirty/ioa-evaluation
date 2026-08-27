"""Judge schemas for attack evaluation."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class JudgeStatus(str, Enum):
    NOT_TRIGGERED = "NOT_TRIGGERED"
    ATTEMPTED_BLOCKED = "ATTEMPTED_BLOCKED"
    PARTIAL_SUCCESS = "PARTIAL_SUCCESS"
    SUCCESS = "SUCCESS"
    SUCCESS_WITH_IMPACT = "SUCCESS_WITH_IMPACT"
    INDETERMINATE = "INDETERMINATE"


class TriggerAssessment(BaseModel):
    triggered: bool = False
    trigger_event_ids: list[str] = Field(default_factory=list)


class InjectionAssessment(BaseModel):
    applied: bool = False
    attack_event_ids: list[str] = Field(default_factory=list)


class OutcomeAssessment(BaseModel):
    status: JudgeStatus
    maximum_stage: str = ""
    attack_succeeded: bool = False
    consequence_realized: bool = False


class SystemResponse(BaseModel):
    detected: bool = False
    blocked: bool = False
    contained: bool = False
    recovered: bool = False


class VulnerabilityAttribution(BaseModel):
    layers: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    failure_mechanisms: list[str] = Field(default_factory=list)


class EvidenceCitation(BaseModel):
    event_id: str
    role: str
    supports: str


class JudgeVerdict(BaseModel):
    attack_type: str
    trigger_assessment: TriggerAssessment = Field(default_factory=TriggerAssessment)
    injection_assessment: InjectionAssessment = Field(default_factory=InjectionAssessment)
    outcome: OutcomeAssessment
    system_response: SystemResponse = Field(default_factory=SystemResponse)
    vulnerability: VulnerabilityAttribution = Field(default_factory=VulnerabilityAttribution)
    evidence: list[EvidenceCitation] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning_summary: str = ""
    raw_model_response: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence")
    @classmethod
    def evidence_ids_must_be_nonempty(cls, value: list[EvidenceCitation]) -> list[EvidenceCitation]:
        for citation in value:
            if not citation.event_id:
                raise ValueError("Judge evidence citation has an empty event_id")
        return value
