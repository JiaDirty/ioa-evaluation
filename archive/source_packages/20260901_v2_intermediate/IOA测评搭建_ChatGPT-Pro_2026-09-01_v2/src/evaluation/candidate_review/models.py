"""Data contracts for deterministic and model-assisted candidate review."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


FindingSeverity = Literal["ERROR", "WARNING", "INFO"]
ReviewDecision = Literal["ACCEPT", "REVISE", "REJECT"]


class DeterministicFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    severity: FindingSeverity
    location: str
    message: str
    evidence: list[str] = Field(default_factory=list)


class DeterministicReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["candidate_deterministic_review_v1"] = (
        "candidate_deterministic_review_v1"
    )
    candidate_uid: str
    case_id: str
    category_code: str
    category_name_zh: str
    generator_model_id: str
    source_path: str
    passed: bool
    findings: list[DeterministicFinding] = Field(default_factory=list)
    metrics: dict[str, int | float | str | bool] = Field(default_factory=dict)

    @model_validator(mode="after")
    def derive_passed(self) -> "DeterministicReview":
        expected = not any(item.severity == "ERROR" for item in self.findings)
        if self.passed != expected:
            raise ValueError("passed must be false exactly when an ERROR exists")
        return self


class CriterionReview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=1, le=5)
    passed: bool
    reason: str = Field(min_length=8, max_length=800)
    evidence: list[str] = Field(default_factory=list, max_length=5)


class SemanticReview(BaseModel):
    """Strict response contract for one independent semantic reviewer."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["candidate_semantic_review_v1"]
    candidate_uid: str
    case_id: str
    reviewer_model_id: str
    decision: ReviewDecision
    business_plausibility: CriterionReview
    category_alignment: CriterionReview
    causal_contrast: CriterionReview
    logical_consistency: CriterionReview
    tool_and_state_executability: CriterionReview
    objective_scoreability: CriterionReview
    recovery_validity: CriterionReview
    information_hygiene: CriterionReview
    novelty: CriterionReview
    critical_issues: list[str] = Field(default_factory=list, max_length=10)
    revision_suggestions: list[str] = Field(default_factory=list, max_length=10)
    confidence: float = Field(ge=0.0, le=1.0)

    @model_validator(mode="after")
    def validate_decision_consistency(self) -> "SemanticReview":
        criteria = [
            self.business_plausibility,
            self.category_alignment,
            self.causal_contrast,
            self.logical_consistency,
            self.tool_and_state_executability,
            self.objective_scoreability,
            self.recovery_validity,
            self.information_hygiene,
            self.novelty,
        ]
        failed = [item for item in criteria if not item.passed]
        if self.decision == "ACCEPT" and failed:
            raise ValueError("ACCEPT requires every criterion to pass")
        if self.decision == "REJECT" and not self.critical_issues:
            raise ValueError("REJECT requires at least one critical issue")
        if self.decision != "ACCEPT" and not self.revision_suggestions:
            raise ValueError("REVISE/REJECT requires revision suggestions")
        return self


SEMANTIC_CRITERION_FIELDS = (
    "business_plausibility",
    "category_alignment",
    "causal_contrast",
    "logical_consistency",
    "tool_and_state_executability",
    "objective_scoreability",
    "recovery_validity",
    "information_hygiene",
    "novelty",
)
