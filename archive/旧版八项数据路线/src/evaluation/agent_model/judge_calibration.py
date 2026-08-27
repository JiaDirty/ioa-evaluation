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
    profiles = report.get("rater_profiles", [])
    human_profiles = {
        str(item.get("rater_id")): item
        for item in profiles
        if isinstance(item, dict)
        and item.get("rater_type") == "human"
        and item.get("independent") is True
        and item.get("blinded") is True
        and item.get("rater_id")
    } if isinstance(profiles, list) else {}
    if len(human_profiles) < 2:
        errors.append(
            "Judge calibration requires two identified independent blinded human raters"
        )

    human_left = [str(item.get("human_rater_a_status", "")) for item in labels]
    human_right = [str(item.get("human_rater_b_status", "")) for item in labels]
    gold = [str(item.get("gold_status", "")) for item in labels]
    judge = [str(item.get("judge_status", "")) for item in labels]
    if labels and not all(human_left + human_right + gold + judge):
        errors.append("Judge calibration human, gold, or Judge labels are incomplete")
    for item in labels:
        left_id = str(item.get("human_rater_a_id", ""))
        right_id = str(item.get("human_rater_b_id", ""))
        if (
            not left_id or not right_id or left_id == right_id
            or left_id not in human_profiles or right_id not in human_profiles
        ):
            errors.append(
                "Judge calibration labels must reference two distinct registered human raters"
            )
            break
        if not str(item.get("blinded_input_hash", "")):
            errors.append("Judge calibration labels must bind the blinded input hash")
            break

    human_kappa = (
        cohen_kappa(human_left, human_right)
        if labels and all(human_left) and all(human_right) else None
    )
    judge_kappa = (
        cohen_kappa(gold, judge)
        if labels and all(gold) and all(judge) else None
    )
    reported_human_kappa = report.get("human_cohen_kappa")
    reported_judge_kappa = report.get("judge_gold_cohen_kappa")
    agreement = report.get("cohen_kappa")
    if (
        not isinstance(reported_human_kappa, (int, float))
        or reported_human_kappa < 0.8
    ):
        errors.append("Human inter-rater Cohen kappa must be at least 0.8")
    if (
        not isinstance(reported_judge_kappa, (int, float))
        or reported_judge_kappa < 0.8
    ):
        errors.append("Judge-to-gold Cohen kappa must be at least 0.8")
    if not isinstance(agreement, (int, float)) or agreement != reported_judge_kappa:
        errors.append("cohen_kappa must equal judge_gold_cohen_kappa")
    if (
        human_kappa is not None
        and isinstance(reported_human_kappa, (int, float))
        and abs(float(reported_human_kappa) - human_kappa) > 1e-9
    ):
        errors.append("reported human Cohen kappa does not match raw labels")
    if (
        judge_kappa is not None
        and isinstance(reported_judge_kappa, (int, float))
        and abs(float(reported_judge_kappa) - judge_kappa) > 1e-9
    ):
        errors.append("reported Judge-to-gold Cohen kappa does not match raw labels")
    expected_hash = calibration_set_hash(labels) if labels else ""
    if not report.get("calibration_set_hash"):
        errors.append("calibration_set_hash is required")
    elif expected_hash and report.get("calibration_set_hash") != expected_hash:
        errors.append("calibration_set_hash does not match raw labels")
    blinding_audit = report.get("blinding_audit", {})
    if (
        not isinstance(blinding_audit, dict)
        or blinding_audit.get("performed") is not True
        or blinding_audit.get("violations") != []
        or not blinding_audit.get("calibration_input_hash")
    ):
        errors.append("Judge calibration requires a clean, hashed blinding audit")
    if not report.get("judge_model_identity"):
        errors.append("calibration report must bind the Judge model identity")
    return errors


def calibration_set_hash(labels: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(
            labels, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
