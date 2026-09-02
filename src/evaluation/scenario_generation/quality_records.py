"""Offline quality-stage records used by the resumable scenario pipeline.

These records describe results that may later come from Inspect AI, an
independent reviewer, or a human.  They are deliberately data-only: creating
or validating one never creates a model client and never sends a request.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RuntimeCheckRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["runtime_check_v1"] = "runtime_check_v1"
    candidate_uid: str = Field(min_length=1)
    status: Literal["PASS", "FAIL", "NOT_RUN"]
    runner_version: str = Field(min_length=1, max_length=200)
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
    candidate_uid: str = Field(min_length=1)
    reviewer_kind: Literal["model", "human", "external"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    decision: Literal["ACCEPT", "REVISE", "REJECT"]
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
            raise ValueError(
                "semantic review ACCEPT cannot contain failed dimensions: "
                f"{failed}"
            )
        if self.decision in {"REVISE", "REJECT"} and not (self.key_issues or self.recommendations):
            raise ValueError(
                "REVISE/REJECT semantic review must include key_issues or recommendations"
            )
        return self


class HumanDecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["human_decision_v1"] = "human_decision_v1"
    candidate_uid: str = Field(min_length=1)
    decision: Literal["ACCEPT", "REVISE", "REJECT"]
    reviewer_id: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=4000)
    release_membership: list[str] = Field(default_factory=list)
    evidence_paths: list[str] = Field(default_factory=list)
    recorded_at: str = Field(default_factory=_now)


__all__ = [
    "HumanDecisionRecord",
    "ReviewDimension",
    "RuntimeCheckRecord",
    "SemanticReviewRecord",
]
