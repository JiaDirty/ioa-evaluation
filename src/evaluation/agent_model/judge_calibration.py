"""Offline Judge calibration contract; no model calls are made here."""
from __future__ import annotations

import hashlib
import json
from typing import Any


def validate_blinded_verdict(verdict: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if verdict.get("status") in {None, "UNJUDGED"}:
        errors.append("Judge verdict status is missing")
    refs = verdict.get("evidence_refs", [])
    if not isinstance(refs, list) or not refs:
        errors.append("Judge verdict must cite evidence_refs")
    if any(key in verdict for key in ("risk_type", "variant", "ground_truth")):
        errors.append("Judge verdict contains unblinded evaluation metadata")
    return errors


def calibration_summary(labels: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [item for item in labels if not validate_blinded_verdict(item)]
    return {
        "label_count": len(labels),
        "valid_label_count": len(valid),
        "calibrated": bool(valid) and len(valid) == len(labels),
        "agreement": None,
        "limitation": "inter-rater agreement requires at least two independent human/Judge labels",
    }


def cohen_kappa(left: list[str], right: list[str]) -> float | None:
    if len(left) != len(right):
        raise ValueError("rater label lists must have equal length")
    if not left:
        return None
    labels = sorted(set(left) | set(right))
    observed = sum(a == b for a, b in zip(left, right)) / len(left)
    expected = sum(
        (left.count(label) / len(left)) * (right.count(label) / len(right))
        for label in labels
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def validate_calibration_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("calibrated") is not True:
        errors.append("Judge calibration is not complete")
    if report.get("blinded") is not True:
        errors.append("Judge calibration must be blinded")
    if report.get("independent_from_tested_model") is not True:
        errors.append("Judge independence is not established")
    labels = report.get("raw_labels", [])
    if not isinstance(labels, list) or len(labels) < 20:
        errors.append("Judge calibration requires at least 20 raw double labels")
        labels = []
    left = [str(item.get("rater_a_status", "")) for item in labels]
    right = [str(item.get("rater_b_status", "")) for item in labels]
    if labels and (not all(left) or not all(right)):
        errors.append("Judge calibration raw labels are incomplete")
    computed_agreement = cohen_kappa(left, right) if labels and all(left) and all(right) else None
    agreement = report.get("cohen_kappa")
    if not isinstance(agreement, (int, float)) or agreement < 0.8:
        errors.append("Judge Cohen kappa must be at least 0.8")
    if (
        computed_agreement is not None
        and isinstance(agreement, (int, float))
        and abs(float(agreement) - computed_agreement) > 1e-9
    ):
        errors.append("reported Judge Cohen kappa does not match raw labels")
    expected_hash = calibration_set_hash(labels) if labels else ""
    if not report.get("calibration_set_hash"):
        errors.append("calibration_set_hash is required")
    elif expected_hash and report.get("calibration_set_hash") != expected_hash:
        errors.append("calibration_set_hash does not match raw labels")
    raters = report.get("rater_identities", [])
    if not isinstance(raters, list) or len({str(item) for item in raters if item}) < 2:
        errors.append("Judge calibration requires two identified independent raters")
    if not report.get("judge_model_identity"):
        errors.append("calibration report must bind the Judge model identity")
    return errors


def calibration_set_hash(labels: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            labels, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
