"""Deterministic case-level paired summaries with bootstrap intervals."""
from __future__ import annotations

import hashlib
import random
from typing import Any, Iterable


def paired_effect(baseline: Iterable[float], risk: Iterable[float]) -> dict[str, float | int | None]:
    left, right = list(baseline), list(risk)
    if len(left) != len(right):
        raise ValueError("paired samples must have equal length")
    differences = [r - b for b, r in zip(left, right)]
    if not differences:
        return {"n": 0, "mean_difference": None, "invalid_rate": 0.0}
    return {
        "n": len(differences),
        "mean_difference": sum(differences) / len(differences),
        "invalid_rate": 0.0,
    }


def bootstrap_ci(values: Iterable[float], iterations: int = 1000, alpha: float = 0.05) -> dict[str, float | int | None]:
    observations = list(values)
    if not observations:
        return {"n": 0, "lower": None, "upper": None}
    seed = int(hashlib.sha256(repr(observations).encode()).hexdigest()[:16], 16)
    rng = random.Random(seed)
    samples = []
    for _ in range(iterations):
        draw = [observations[rng.randrange(len(observations))] for _ in observations]
        samples.append(sum(draw) / len(draw))
    samples.sort()
    lower_index = max(0, int((alpha / 2) * len(samples)) - 1)
    upper_index = min(len(samples) - 1, int((1 - alpha / 2) * len(samples)))
    return {
        "n": len(observations),
        "lower": samples[lower_index],
        "upper": samples[upper_index],
    }


def category_summaries(
    results: Iterable[Any],
    paired_results: Iterable[Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Report phase-separated case-level summaries and paired effects."""
    result_items = list(results)
    grouped: dict[str, list[Any]] = {}
    case_to_risk: dict[str, str] = {}
    for result in result_items:
        risk_type = str(result.risk_type)
        grouped.setdefault(risk_type, []).append(result)
        case_to_risk[str(result.case_id)] = risk_type

    pair_groups: dict[str, list[Any]] = {}
    for pair in list(paired_results or []):
        risk_type = case_to_risk.get(str(pair.case_id))
        if risk_type:
            pair_groups.setdefault(risk_type, []).append(pair)

    summaries: dict[str, dict[str, Any]] = {}
    for risk_type, items in grouped.items():
        by_variant: dict[str, dict[str, Any]] = {}
        for variant in ("baseline", "risk", "recovery"):
            variant_items = [item for item in items if item.variant == variant]
            levels = sorted({str(item.experiment_level) for item in variant_items})
            by_variant[variant] = {
                "by_experiment_level": {
                    level: _summarize_run_group([
                        item for item in variant_items
                        if str(item.experiment_level) == level
                    ])
                    for level in levels
                }
            }

        summaries[risk_type] = {
            "by_variant": by_variant,
            "paired_effects": _summarize_paired_effects(
                pair_groups.get(risk_type, [])
            ),
            "aggregation_unit": "case",
            "repeat_handling": (
                "valid repeats are averaged within each case before category statistics"
            ),
            "sensitivity_note": (
                "INVALID runs remain in denominators and are reported separately; "
                "they are never converted to safe outcomes or numeric zeroes."
            ),
        }
    return summaries


def _summarize_run_group(items: list[Any]) -> dict[str, Any]:
    invalid = [item for item in items if item.status == "INVALID"]
    by_case_metric: dict[str, dict[str, list[float]]] = {}
    status_distribution: dict[str, int] = {}
    for item in items:
        status_distribution[str(item.status)] = (
            status_distribution.get(str(item.status), 0) + 1
        )
        if item.status == "INVALID":
            continue
        case_metrics = by_case_metric.setdefault(str(item.case_id), {})
        for name, value in item.objective_metrics.items():
            if isinstance(value, bool):
                case_metrics.setdefault(name, []).append(float(value))
            elif isinstance(value, (int, float)):
                case_metrics.setdefault(name, []).append(float(value))

    numeric: dict[str, list[float]] = {}
    for case_metrics in by_case_metric.values():
        for name, repeats in case_metrics.items():
            numeric.setdefault(name, []).append(sum(repeats) / len(repeats))
    return {
        "total_runs": len(items),
        "case_count": len({str(item.case_id) for item in items}),
        "invalid_count": len(invalid),
        "invalid_rate": len(invalid) / len(items) if items else None,
        "failure_codes": sorted({
            str(item.judge_verdict.get("status"))
            for item in invalid if item.judge_verdict.get("status")
        }),
        "status_distribution": dict(sorted(status_distribution.items())),
        "metric_distributions": {
            name: {
                "case_values": values,
                "case_count": len(values),
                "mean": sum(values) / len(values),
                "bootstrap_ci": bootstrap_ci(values),
            }
            for name, values in sorted(numeric.items())
        },
    }


def _summarize_paired_effects(items: list[Any]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for level in sorted({str(item.experiment_level) for item in items}):
        level_items = [
            item for item in items
            if str(item.experiment_level) == level
            and item.formal_aggregate_eligible
        ]
        effects: dict[str, dict[str, list[float]]] = {
            "baseline_to_risk": {},
            "risk_to_recovery": {},
        }
        for item in level_items:
            for name, value in item.baseline_risk_delta.items():
                effects["baseline_to_risk"].setdefault(name, []).append(float(value))
            for name, value in item.risk_recovery_delta.items():
                effects["risk_to_recovery"].setdefault(name, []).append(float(value))
        output[level] = {
            direction: {
                name: {
                    "paired_values": values,
                    "pair_count": len(values),
                    "mean_difference": sum(values) / len(values),
                    "bootstrap_ci": bootstrap_ci(values),
                }
                for name, values in sorted(metrics.items())
            }
            for direction, metrics in effects.items()
        }
    return output
