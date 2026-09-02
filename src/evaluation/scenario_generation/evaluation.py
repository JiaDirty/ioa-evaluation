"""Evaluation running, scoring and release assembly.

The runtime engine lives in ``business_protocol``; this module is the
pipeline's only integration point with it.  Offline runs use the scripted
protocol client and never call a model; live runs require an explicit opt-in.
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..business_protocol.models import BusinessCaseSpec, PairedCaseRunResult
from ..business_protocol.runner import BusinessProtocolRunner
from ..business_protocol.scripted_client import ProtocolValidationClient
from .catalog import load_evaluation_catalog
from .models import (
    CompiledCase,
    HumanDecisionRecord,
    RuntimeCheckRecord,
    SemanticReviewRecord,
    verify_compiled_case_hash,
)


RUNNER_VERSION = "business_protocol_runner_v1"


def run_offline_case(case: BusinessCaseSpec) -> list[PairedCaseRunResult]:
    """Run one compiled case offline through the scripted protocol client.

    Returns one paired result per run level (key_node, full_chain).  No model
    request is made.
    """

    runner = BusinessProtocolRunner(ProtocolValidationClient())

    async def _run() -> list[PairedCaseRunResult]:
        results: list[PairedCaseRunResult] = []
        for level in ("key_node", "full_chain"):
            results.append(await runner.run_paired_case(case, run_level=level))
        return results

    return asyncio.run(_run())


def build_runtime_check_record(
    task_id: str,
    compiled: CompiledCase,
    results: list[PairedCaseRunResult],
    *,
    errors: list[str] | None = None,
) -> RuntimeCheckRecord:
    """Convert paired run results into a hash-bound runtime check record."""

    errors = list(errors or [])
    status = "PASS" if all(
        run.validity == "VALID"
        for item in results
        for run in (item.baseline, item.mechanism, item.recovery)
    ) and not errors else "FAIL"
    run_level_results = {
        item.run_level: {
            "validity": {
                "baseline": item.baseline.validity,
                "mechanism": item.mechanism.validity,
                "recovery": item.recovery.validity,
            },
            "baseline_safety": (
                item.baseline.safety_outcome if item.baseline else None
            ),
            "mechanism_safety": (
                item.mechanism.safety_outcome if item.mechanism else None
            ),
            "recovery_safety": (
                item.recovery.safety_outcome if item.recovery else None
            ),
        }
        for item in results
    }
    return RuntimeCheckRecord(
        task_id=task_id,
        status=status,
        runner_version=RUNNER_VERSION,
        kernel_sha256=compiled.kernel_sha256,
        effect_sha256=compiled.effect_sha256,
        compiled_case_sha256=verify_compiled_case_hash(compiled),
        run_level_results=run_level_results,
        summary=f"离线运行 {len(results)} 个运行层级，有效性 {status}。",
        errors=errors,
    )


def validate_semantic_reviews(
    task_id: str,
    compiled: CompiledCase,
    reviews: list[SemanticReviewRecord],
) -> str:
    """Validate review independence and hash binding; raise on mismatch.

    Two semantic reviews must come from two different reviewer IDs and both
    must bind the same compiled-case hash.
    """

    if len(reviews) < 2:
        raise ValueError("semantic review requires at least two independent reviews")
    reviewer_ids = {item.reviewer_id for item in reviews}
    if len(reviewer_ids) < 2:
        raise ValueError("two semantic reviews must come from different reviewer IDs")
    compiled_hash = verify_compiled_case_hash(compiled)
    for review in reviews:
        if review.task_id != task_id:
            raise ValueError(f"review {review.reviewer_id} binds a different task")
        if review.kernel_sha256 != compiled.kernel_sha256:
            raise ValueError(f"review {review.reviewer_id} binds a different kernel hash")
        if review.effect_sha256 != compiled.effect_sha256:
            raise ValueError(f"review {review.reviewer_id} binds a different effect hash")
        if review.compiled_case_sha256 != compiled_hash:
            raise ValueError(f"review {review.reviewer_id} binds a different compiled hash")
    if all(item.decision == "ACCEPT" for item in reviews):
        return "ACCEPT"
    if any(item.decision == "REJECT" for item in reviews):
        return "REJECT"
    return "REVISE"


def validate_human_decision(
    task_id: str,
    compiled: CompiledCase,
    decision: HumanDecisionRecord,
) -> None:
    if decision.task_id != task_id:
        raise ValueError("human decision binds a different task")
    if decision.kernel_sha256 != compiled.kernel_sha256:
        raise ValueError("human decision binds a different kernel hash")
    if decision.effect_sha256 != compiled.effect_sha256:
        raise ValueError("human decision binds a different effect hash")
    if decision.compiled_case_sha256 != verify_compiled_case_hash(compiled):
        raise ValueError("human decision binds a different compiled hash")


def select_release_members(
    *,
    frozen_entries: list[dict[str, Any]],
    catalog_path: Any = None,
) -> dict[str, list[str]]:
    """Pick up to the per-branch quota of frozen cases for a release.

    Returns ``{branch_id: [task_id, ...]}``.  Frozen entries are taken in
    registry event order (first frozen first).  Branches without enough frozen
    members are simply missing from the result; the caller decides whether the
    release is complete.
    """

    catalog = load_evaluation_catalog() if catalog_path is None else catalog_path
    selection: dict[str, list[str]] = {}
    for entry in frozen_entries:
        branch_id = entry.get("branch_id")
        if branch_id is None:
            continue
        quota = catalog.release_quota.get(branch_id)
        if quota is None:
            continue
        selected = selection.setdefault(branch_id, [])
        if len(selected) < quota:
            selected.append(entry["task_id"])
    return selection


__all__ = [
    "RUNNER_VERSION",
    "build_runtime_check_record",
    "run_offline_case",
    "select_release_members",
    "validate_human_decision",
    "validate_semantic_reviews",
]
