from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from src.evaluation.business_protocol.loader import load_business_cases
from src.evaluation.candidate_review.deterministic import (
    CandidateRecord,
    audit_candidates,
)
from src.evaluation.candidate_review.models import CriterionReview, SemanticReview


def _record(case, model="generator-a"):
    return CandidateRecord(
        case=case,
        source_path=Path("candidate.jsonl"),
        generator_model_id=model,
        item_name="item",
        batch_id="item__第01条",
    )


def _criterion(passed=True):
    return CriterionReview(
        score=5 if passed else 2,
        passed=passed,
        reason="证据和业务逻辑已经逐项核对。",
        evidence=["steps[0]"],
    )


def test_deterministic_audit_accepts_current_formal_case():
    case = next(iter(load_business_cases().values()))
    reviews, _ = audit_candidates([_record(case)])
    assert reviews[0].passed
    assert not [item for item in reviews[0].findings if item.severity == "ERROR"]


def test_deterministic_audit_detects_future_information():
    case = deepcopy(next(iter(load_business_cases().values())))
    step = case.steps[0]
    record = step.inputs["baseline"].records[0]
    record.created_at = "2099-01-01T00:00:00+08:00"
    reviews, _ = audit_candidates([_record(case)])
    assert not reviews[0].passed
    assert "FUTURE_INFORMATION" in {item.code for item in reviews[0].findings}


def test_duplicate_content_is_an_error():
    case = next(iter(load_business_cases().values()))
    other = case.model_copy(deep=True)
    other.case_id = f"{case.case_id}-copy"
    reviews, duplicates = audit_candidates([_record(case), _record(other, "generator-b")])
    assert any(item["kind"] == "EXACT_CONTENT" for item in duplicates)
    assert not all(item.passed for item in reviews)


def test_semantic_accept_requires_every_criterion_to_pass():
    payload = {
        "schema_version": "candidate_semantic_review_v1",
        "candidate_uid": "item::generator::case-1",
        "case_id": "case-1",
        "reviewer_model_id": "reviewer",
        "decision": "ACCEPT",
        "critical_issues": [],
        "revision_suggestions": [],
        "confidence": 0.9,
    }
    for name in (
        "business_plausibility", "category_alignment", "causal_contrast",
        "logical_consistency", "tool_and_state_executability",
        "objective_scoreability", "recovery_validity", "information_hygiene",
        "novelty",
    ):
        payload[name] = _criterion()
    review = SemanticReview.model_validate(payload)
    assert review.decision == "ACCEPT"
