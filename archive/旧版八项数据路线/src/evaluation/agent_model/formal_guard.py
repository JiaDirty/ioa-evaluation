"""Fail-closed guards for formal Agent Model v2 runs."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any
from .judge_calibration import validate_calibration_report
from .controls import controls_passed


class FormalRunGuardError(ValueError):
    """Raised when a run is not eligible for formal scoring."""


@dataclass(frozen=True)
class FormalRunConfig:
    run_purpose: str
    execution_mode: str
    variants: list[str]
    judge_configured: bool
    fake_model: bool = False
    manifest: dict[str, Any] | None = None


def validate_formal_run(config: FormalRunConfig) -> None:
    """Validate formal-score prerequisites.

    Dev/smoke runs are allowed through.  Formal runs fail closed unless they
    are live, paired, judged, and manifest-backed.
    """
    if config.run_purpose != "formal":
        return
    if config.fake_model or config.execution_mode != "agentic_live":
        raise FormalRunGuardError("formal runs require agentic_live and cannot use fake/offline model")
    if config.variants != ["baseline", "risk", "recovery"]:
        raise FormalRunGuardError("formal runs require paired baseline/risk/recovery variants")
    if not config.judge_configured:
        raise FormalRunGuardError("formal runs require an independent semantic Judge")
    manifest = config.manifest or {}
    required = {
        "git_commit",
        "dirty_diff_hash",
        "dataset_hash",
        "case_hashes",
        "code_hash",
        "package_lock_hash",
        "environment",
        "model_config_hash",
        "tool_manifest_hash",
        "prompt_hash",
        "fixture_policy_hash",
        "topology_hash",
        "resolved_execution_config",
        "dataset_split_hash",
        "dataset_provenance",
        "planned_order",
        "formal_eligibility_rules_version",
        "formal_plan",
        "formal_plan_hash",
        "tested_model_identity",
        "judge_model_identity",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise FormalRunGuardError(f"formal run manifest missing fields: {missing}")
    empty = sorted(
        key for key in required
        if manifest.get(key) in (None, "", {}, [])
    )
    if empty:
        raise FormalRunGuardError(f"formal run manifest has empty fields: {empty}")
    formal_plan = manifest.get("formal_plan", {})
    expected_plan_hash = hashlib.sha256(
        json.dumps(
            formal_plan, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
    ).hexdigest()
    if manifest.get("formal_plan_hash") != expected_plan_hash:
        raise FormalRunGuardError("formal plan hash does not match its contents")
    registered_cases = set(
        manifest.get("dataset_split", {}).get("formal_evaluation", [])
    )
    if set(formal_plan.get("case_ids", [])) != registered_cases:
        raise FormalRunGuardError(
            "formal plan must cover the complete registered evaluation set"
        )
    calibration_errors = validate_calibration_report(
        manifest.get("judge_calibration", {})
    )
    if calibration_errors:
        raise FormalRunGuardError("; ".join(calibration_errors))
    if not controls_passed(manifest.get("control_results", {})):
        raise FormalRunGuardError("all four deterministic evaluation controls must pass")
    tested_identity = _model_identity_key(manifest.get("tested_model_identity", {}))
    judge_identity = _model_identity_key(manifest.get("judge_model_identity", {}))
    if not tested_identity or not judge_identity:
        raise FormalRunGuardError("formal runs require complete tested and Judge model identities")
    if tested_identity == judge_identity:
        raise FormalRunGuardError(
            "formal runs require a Judge model independent from the tested model"
        )
    calibration_identity = _model_identity_key(
        manifest.get("judge_calibration", {}).get("judge_model_identity", {})
    )
    if calibration_identity != judge_identity:
        raise FormalRunGuardError(
            "Judge calibration identity does not match the configured Judge"
        )
    provenance = manifest.get("dataset_provenance", {})
    if provenance.get("all_cases_seen_during_evaluator_development") is not True:
        raise FormalRunGuardError(
            "dataset provenance must explicitly declare development exposure"
        )
    if provenance.get("held_out_case_count") != 0:
        raise FormalRunGuardError(
            "this v2 manifest cannot claim a held-out subset after full development exposure"
        )
    split = manifest.get("dataset_split", {})
    if split.get("held_out"):
        raise FormalRunGuardError(
            "development-exposed v2 cases cannot be labeled held out"
        )


def validate_formal_coverage(
    results: list[Any],
    paired_results: list[Any],
    manifest: dict[str, Any],
) -> list[str]:
    """Verify that a formal result exactly covers its pre-registered plan."""
    plan = manifest.get("formal_plan", {})
    case_ids = list(plan.get("case_ids", []))
    repeats = dict(plan.get("repeat_count_by_case", {}))
    levels = dict(plan.get("experiment_levels_by_case", {}))
    variants = list(plan.get("variants", []))
    errors: list[str] = []
    if not case_ids or variants != ["baseline", "risk", "recovery"]:
        return ["formal plan is missing cases or the three ordered variants"]
    registered_cases = set(
        manifest.get("dataset_split", {}).get("formal_evaluation", [])
    )
    if set(case_ids) != registered_cases:
        errors.append(
            "formal plan does not cover the complete registered evaluation set"
        )

    expected_pairs: set[tuple[str, int, str]] = set()
    expected_runs: set[tuple[str, int, str, str]] = set()
    expected_order: list[tuple[str, str, str]] = []
    for case_id in case_ids:
        repeat_count = repeats.get(case_id)
        case_levels = levels.get(case_id)
        if not isinstance(repeat_count, int) or repeat_count <= 0:
            errors.append(f"formal plan has invalid repeat count for {case_id}")
            continue
        if not isinstance(case_levels, list) or not case_levels:
            errors.append(f"formal plan has no experiment level for {case_id}")
            continue
        for level in case_levels:
            for repeat_index in range(repeat_count):
                expected_pairs.add((case_id, repeat_index, str(level)))
                for variant in variants:
                    expected_runs.add((case_id, repeat_index, str(level), variant))
                    expected_order.append((case_id, variant, str(level)))

    actual_pairs = {
        (str(item.case_id), int(item.repeat_index), str(item.experiment_level))
        for item in paired_results
    }
    actual_runs = {
        (
            str(item.case_id),
            _repeat_index_from_result(item, paired_results),
            str(item.experiment_level),
            str(item.variant),
        )
        for item in results
    }
    if actual_pairs != expected_pairs:
        errors.append(
            "formal paired-unit coverage does not match the pre-registered plan"
        )
    if actual_runs != expected_runs:
        errors.append("formal run coverage does not match the pre-registered plan")

    actual_order = [
        (
            str(item.get("case_id", "")),
            str(item.get("variant", "")),
            str(item.get("experiment_level", "")),
        )
        for item in manifest.get("actual_order", [])
    ]
    if actual_order != expected_order:
        errors.append("actual run order does not match the pre-registered order")
    return errors


def _repeat_index_from_result(result: Any, paired_results: list[Any]) -> int:
    for pair in paired_results:
        if result.run_id in {
            pair.baseline_run_id, pair.risk_run_id, pair.recovery_run_id,
        }:
            return int(pair.repeat_index)
    return -1


def _model_identity_key(identity: dict[str, Any]) -> tuple[str, str, str] | None:
    provider = str(identity.get("provider", "")).strip().lower()
    model = str(identity.get("model", "")).strip().lower()
    endpoint_hash = str(identity.get("endpoint_hash", "")).strip().lower()
    if not provider or not model or not endpoint_hash:
        return None
    return provider, model, endpoint_hash
