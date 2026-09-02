#!/usr/bin/env python
"""The single formal command for the IOA scenario data production pipeline.

    python scripts/run_pipeline.py import
    python scripts/run_pipeline.py process --all
    python scripts/run_pipeline.py resume --all
    python scripts/run_pipeline.py validate --all
    python scripts/run_pipeline.py evaluate --all
    python scripts/run_pipeline.py review --task <task-id> --reviews <file>
    python scripts/run_pipeline.py freeze --release v1
    python scripts/run_pipeline.py status

Every production action flows through the one PipelineOrchestrator and the one
PipelineRegistry.  Live provider calls only happen with ``--allow-live-api``.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.migrations.build_tasks import build_all_tasks  # noqa: E402
from src.evaluation.scenario_generation import (  # noqa: E402
    ArtifactStore,
    CompiledCase,
    HumanDecisionRecord,
    PipelineOrchestrator,
    ScenarioTask,
    SemanticReviewRecord,
    StageCallConfig,
    TaskProvenance,
    load_evaluation_catalog,
    select_release_members,
    verify_compiled_case_hash,
)
from src.evaluation.scenario_generation.evaluation import run_offline_case  # noqa: E402
from src.evaluation.business_protocol.models import BusinessCaseSpec  # noqa: E402

DATA_ROOT = PROJECT_ROOT / "data"
CATALOG_DIR = DATA_ROOT / "catalog"
RAW_ROOT = DATA_ROOT / "raw"
REFERENCE_RAW = RAW_ROOT / "reference_sources"
CANDIDATE_RAW = RAW_ROOT / "candidate_sources"
WORKSPACE_ROOT = DATA_ROOT / "workspace"
RELEASES_ROOT = DATA_ROOT / "releases"


def _json_out(payload: dict[str, Any], *, stream=None) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2), file=stream)


def _orchestrator() -> PipelineOrchestrator:
    return PipelineOrchestrator(WORKSPACE_ROOT, raw_root=RAW_ROOT)


def _iter_tasks(args: argparse.Namespace, orchestrator: PipelineOrchestrator) -> list[str]:
    entries = orchestrator.registry.entries()
    ids = sorted(entries)
    if args.task:
        if args.task not in ids:
            raise SystemExit(f"unknown task: {args.task}")
        return [args.task]
    if args.branch:
        selected = [
            task_id for task_id, entry in entries.items()
            if entry.branch_id == args.branch
        ]
        if not selected:
            raise SystemExit(f"no tasks for branch: {args.branch}")
        return sorted(selected)
    return ids


def cmd_import(args: argparse.Namespace) -> int:
    """Submit task cards for every raw source (references + candidates)."""

    reference_source = REFERENCE_RAW
    candidate_source = CANDIDATE_RAW / "批量生成-第01轮"
    if not reference_source.is_dir():
        raise SystemExit("raw reference sources missing (data/raw/reference_sources)")
    if not candidate_source.is_dir():
        raise SystemExit("raw candidate sources missing (data/raw/candidate_sources/批量生成-第01轮)")

    tasks = build_all_tasks(
        reference_dir=reference_source,
        candidate_dir=candidate_source,
        raw_root=RAW_ROOT,
    )
    orchestrator = _orchestrator()
    for task in tasks:
        if args.dry_run:
            continue
        orchestrator.submit(task)
    # Reference cases carry frozen scoring vectors: derive their real
    # behaviour oracle deterministically so they compile like every other case.
    reference_tasks = [task for task in tasks if task.provenance.origin == "reference"]
    for task in reference_tasks:
        if args.dry_run:
            continue
        existing_entry = orchestrator.registry.get(task.task_id)
        if existing_entry.stage not in {
            "TASK_READY", "KERNEL_DRAFT", "KERNEL_NEEDS_REVISION", "GENERATION_FAILED"
        }:
            continue
        case = orchestrator._reference_case(task)
        if case is None:
            raise SystemExit(f"reference task {task.task_id} has no extractable case")
        material = next(item for item in task.reference_material if item.kind == "case_jsonl")
        from scripts.migrations.reference_case_conversion import build_reference_kernel_effect

        kernel, effect = build_reference_kernel_effect(
            case,
            candidate_uid=task.task_id,
            source_path=str(RAW_ROOT / material.source_path),
            source_sha256=material.source_sha256,
        )
        orchestrator.submit_kernel(task.task_id, kernel, reason="reference kernel with derived oracle")
        orchestrator.submit_effect(task.task_id, effect, reason="reference effect with derived oracle")
    if args.dry_run:
        _json_out({"status": "IMPORT_DRY_RUN", "task_count": len(tasks)})
        return 0
    _json_out(
        {
            "status": "IMPORTED",
            "task_count": len(tasks),
            "registry": str(orchestrator.registry.path),
            "reference_count": len(reference_tasks),
            "candidate_count": len(tasks) - len(reference_tasks),
        }
    )
    return 0


def cmd_process(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    task_ids = _iter_tasks(args, orchestrator)
    config = _generation_config(args)
    results: list[dict[str, Any]] = []
    failed: list[str] = []
    for task_id in task_ids:
        if args.dry_run:
            entry = orchestrator.registry.get(task_id)
            results.append({"task_id": task_id, "stage": entry.stage, "dry_run": True})
            continue
        try:
            entry = orchestrator.process(
                task_id,
                allow_live_api=args.allow_live_api,
                generation_config=config,
            )
            results.append({"task_id": task_id, "stage": entry.stage})
        except Exception as exc:  # noqa: BLE001 - record and continue batch
            failed.append(task_id)
            results.append({"task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
    counts = Counter(item.get("stage", item.get("error", "?")) for item in results)
    blocked_stages = {
        "TASK_READY",
        "KERNEL_DRAFT",
        "KERNEL_NEEDS_REVISION",
        "KERNEL_READY",
        "EFFECT_DRAFT",
        "EFFECT_NEEDS_REVISION",
        "GENERATION_FAILED",
        "VALIDATION_FAILED",
    }
    has_blocked = any(item.get("stage") in blocked_stages for item in results)
    payload: dict[str, Any] = {
        "status": "PIPELINE_COMPLETED" if not failed and not has_blocked else "PIPELINE_PARTIAL",
        "task_count": len(task_ids),
        "failed_count": len(failed),
        "failed_tasks": failed,
        "stage_counts": dict(sorted(counts.items())),
        "dry_run": bool(args.dry_run),
    }
    _json_out(payload)
    return 0 if not failed and not has_blocked else 3


def cmd_resume(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    task_ids = _iter_tasks(args, orchestrator)
    config = _generation_config(args)
    results: list[dict[str, Any]] = []
    failed: list[str] = []
    for task_id in task_ids:
        if args.dry_run:
            results.append({"task_id": task_id, "stage": orchestrator.registry.get(task_id).stage, "dry_run": True})
            continue
        try:
            entry = orchestrator.resume(
                task_id,
                allow_live_api=args.allow_live_api,
                generation_config=config,
            )
            results.append({"task_id": task_id, "stage": entry.stage})
        except Exception as exc:  # noqa: BLE001
            failed.append(task_id)
            results.append({"task_id": task_id, "error": f"{type(exc).__name__}: {exc}"})
    counts = Counter(item.get("stage", item.get("error", "?")) for item in results)
    _json_out(
        {
            "status": "RESUME_COMPLETED" if not failed else "RESUME_PARTIAL",
            "task_count": len(task_ids),
            "failed_count": len(failed),
            "failed_tasks": failed,
            "stage_counts": dict(sorted(counts.items())),
            "dry_run": bool(args.dry_run),
        }
    )
    return 0 if not failed else 3


def cmd_validate(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    task_ids = _iter_tasks(args, orchestrator)
    advanced = 0
    failed: list[str] = []
    for task_id in task_ids:
        entry = orchestrator.registry.get(task_id)
        if args.dry_run:
            continue
        try:
            if entry.stage == "COMPILED":
                orchestrator.validate_paths(task_id)
                advanced += 1
            elif entry.stage == "PATH_VALID":
                orchestrator.validate_runtime(task_id)
                advanced += 1
        except Exception as exc:  # noqa: BLE001
            failed.append(task_id)
            orchestrator.registry.record_error(task_id, f"validate failed: {type(exc).__name__}: {exc}")
    blocked = sum(
        1
        for task_id in task_ids
        if orchestrator.registry.get(task_id).stage
        in {"TASK_READY", "KERNEL_DRAFT", "KERNEL_NEEDS_REVISION", "KERNEL_READY", "EFFECT_DRAFT", "EFFECT_NEEDS_REVISION", "GENERATION_FAILED", "VALIDATION_FAILED"}
    )
    _json_out(
        {
            "status": "VALIDATED" if not failed and blocked == 0 else "VALIDATION_PARTIAL",
            "advanced": advanced,
            "blocked_count": blocked,
            "failed_tasks": failed,
            "dry_run": bool(args.dry_run),
        }
    )
    return 0 if not failed and blocked == 0 else 3


def cmd_evaluate(args: argparse.Namespace) -> int:
    """Single evaluation entry: run compiled cases offline (or validate only)."""

    orchestrator = _orchestrator()
    task_ids = _iter_tasks(args, orchestrator)
    store = ArtifactStore(WORKSPACE_ROOT)
    results: list[dict[str, Any]] = []
    for task_id in task_ids:
        entry = orchestrator.registry.get(task_id)
        if "compiled" not in entry.artifacts:
            continue
        compiled = CompiledCase.model_validate_json(store.read_text(entry.artifacts["compiled"]))
        compiled = compiled.model_copy(update={"case": BusinessCaseSpec.model_validate(compiled.case)})
        verify_compiled_case_hash(compiled)
        if args.validate_only:
            results.append(
                {"task_id": task_id, "case_id": compiled.case_id, "status": "VALID"}
            )
            continue
        if args.dry_run:
            results.append({"task_id": task_id, "case_id": compiled.case_id, "dry_run": True})
            continue
        paired = run_offline_case(compiled.case)
        results.append(
            {
                "task_id": task_id,
                "case_id": compiled.case_id,
                "validity": [
                    {
                        "baseline": item.baseline.validity,
                        "mechanism": item.mechanism.validity,
                        "recovery": item.recovery.validity,
                    }
                    for item in paired
                ],
                "baseline_safety": [item.baseline.safety_outcome for item in paired],
                "mechanism_safety": [item.mechanism.safety_outcome for item in paired],
                "recovery_safety": [item.recovery.safety_outcome for item in paired],
            }
        )
    _json_out(
        {
            "status": "EVALUATED",
            "result_count": len(results),
            "validate_only": bool(args.validate_only),
            "dry_run": bool(args.dry_run),
            "results": results,
        }
    )
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    payload = json.loads(Path(args.reviews).read_text(encoding="utf-8"))
    reviews = (
        payload if isinstance(payload, list) else payload.get("reviews", [])
    )
    parsed = [SemanticReviewRecord.model_validate(item) for item in reviews]
    entry = orchestrator.record_semantic_reviews(args.task, parsed)
    _json_out({"status": "REVIEWED", "task_id": args.task, "stage": entry.stage})
    return 0


def cmd_human(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    store = ArtifactStore(WORKSPACE_ROOT)
    entry = orchestrator.registry.get(args.task)
    if "compiled" not in entry.artifacts:
        raise SystemExit(f"task {args.task} has no compiled case")
    compiled = CompiledCase.model_validate_json(store.read_text(entry.artifacts["compiled"]))
    compiled = compiled.model_copy(update={"case": BusinessCaseSpec.model_validate(compiled.case)})
    decision = HumanDecisionRecord(
        task_id=args.task,
        decision=args.decision.upper(),
        reviewer_id=args.reviewer,
        reason=args.reason,
        kernel_sha256=compiled.kernel_sha256,
        effect_sha256=compiled.effect_sha256,
        compiled_case_sha256=verify_compiled_case_hash(compiled),
        release_membership=args.release_membership.split(",") if args.release_membership else [],
    )
    entry = orchestrator.record_human_decision(args.task, decision)
    _json_out({"status": "HUMAN_DECIDED", "task_id": args.task, "stage": entry.stage})
    return 0


def cmd_freeze(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    release_name = args.release or "v1"
    store = ArtifactStore(WORKSPACE_ROOT)
    task_ids = _iter_tasks(args, orchestrator)
    for task_id in task_ids:
        entry = orchestrator.registry.get(task_id)
        if entry.stage == "HUMAN_ACCEPTED" and not args.dry_run:
            orchestrator.freeze(task_id)
    entries = orchestrator.registry.entries()
    frozen = [
        {
            "task_id": task_id,
            "branch_id": entry.branch_id,
            "case_id": entry.case_id,
        }
        for task_id, entry in sorted(entries.items())
        if entry.stage == "FROZEN"
    ]
    selection = select_release_members(frozen_entries=frozen)
    catalog = load_evaluation_catalog()
    missing = sorted(set(catalog.branch_ids) - set(selection))
    release_dir = RELEASES_ROOT / release_name
    manifest: dict[str, Any] = {
        "schema_version": "release_manifest_v1",
        "release": release_name,
        "branch_counts": {branch: len(tasks) for branch, tasks in selection.items()},
        "total_cases": sum(len(tasks) for tasks in selection.values()),
        "quota": catalog.release_quota,
        "missing_branches": missing,
        "cases": {},
    }
    if not args.dry_run:
        cases_dir = release_dir / "cases"
        cases_dir.mkdir(parents=True, exist_ok=True)
        for branch_id, ids in sorted(selection.items()):
            for task_id in ids:
                entry = entries[task_id]
                compiled = CompiledCase.model_validate_json(
                    store.read_text(entry.artifacts["compiled"])
                )
                case_name = f"{compiled.case_id}.json"
                shutil.copy2(store.path(entry.artifacts["compiled"]), cases_dir / case_name)
                manifest["cases"][branch_id] = manifest["cases"].get(branch_id, []) + [case_name]
        (release_dir / "dataset_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    _json_out(
        {
            "status": "RELEASE_SELECTED",
            "release": release_name,
            "release_dir": str(release_dir),
            "branch_counts": manifest["branch_counts"],
            "total_cases": manifest["total_cases"],
            "missing_branches": missing,
            "dry_run": bool(args.dry_run),
        }
    )
    return 0 if not missing else 4


def cmd_generate(args: argparse.Namespace) -> int:
    """Generate missing release cases for one branch via the stable API flow."""

    if not args.allow_live_api:
        raise SystemExit("generation requires --allow-live-api")
    catalog = load_evaluation_catalog()
    branch = catalog.branch_for_id(args.branch)
    orchestrator = _orchestrator()
    config = _generation_config(args)
    generated = 0
    for index in range(1, args.count + 1):
        task = ScenarioTask.create(
            task_id=f"task-gen-{args.branch}-{index:03d}",
            branch_id=args.branch,
            objective=(
                f"为测评分支 {args.branch}（{branch.name_zh}）生成一个新的业务场景，"
                f"覆盖机制：{branch.name_zh}。场景必须与已冻结案例明显不同。"
            ),
            mechanism_requirements=[
                next(
                    item.mechanism
                    for item in catalog.categories
                    if item.code == branch.category
                )
            ],
            provenance=TaskProvenance(
                origin="generated",
                model_id=config.model_id if config else None,
            ),
            metadata={"generation_round": str(index)},
        )
        orchestrator.submit(task)
        orchestrator.generate_kernel(task.task_id, config=config, allow_live_api=True)
        orchestrator.generate_effect(task.task_id, config=config, allow_live_api=True)
        orchestrator.process(task.task_id, allow_live_api=True, generation_config=config)
        generated += 1
    _json_out({"status": "GENERATED", "branch": args.branch, "count": generated})
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    orchestrator = _orchestrator()
    entries = orchestrator.registry.entries()
    counts = Counter(entry.stage for entry in entries.values())
    branch_counts: Counter[str] = Counter()
    for entry in entries.values():
        if entry.branch_id:
            branch_counts[entry.branch_id] += 1
    payload: dict[str, Any] = {
        "status": "STATUS",
        "registry": str(orchestrator.registry.path),
        "task_count": len(entries),
        "stage_counts": dict(sorted(counts.items())),
        "branch_task_counts": dict(sorted(branch_counts.items())),
        "event_count": len(orchestrator.registry.data.events),
    }
    if args.task:
        payload["task"] = {
            args.task: entries[args.task].model_dump(mode="json"),
        }
    _json_out(payload)
    return 0


def _generation_config(args: argparse.Namespace) -> StageCallConfig | None:
    if not args.allow_live_api:
        return None
    return StageCallConfig(
        model_id=args.model,
        reasoning_effort=args.reasoning_effort,
        seed=args.seed,
        retry_count=args.retry_count,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_import = sub.add_parser("import", help="copy raw sources and submit task cards")
    p_import.add_argument("--dry-run", action="store_true")
    p_import.set_defaults(func=cmd_import)

    for name in ("process", "resume", "validate"):
        p = sub.add_parser(name, help=f"{name} tasks")
        _add_task_selectors(p)
        p.add_argument("--allow-live-api", action="store_true")
        p.add_argument("--model", default="gpt-4o-mini")
        p.add_argument("--reasoning-effort")
        p.add_argument("--seed", type=int)
        p.add_argument("--retry-count", type=int, default=1)
        p.add_argument("--dry-run", action="store_true")
        p.set_defaults(func={"process": cmd_process, "resume": cmd_resume, "validate": cmd_validate}[name])

    p_eval = sub.add_parser("evaluate", help="run compiled cases through the single evaluation entry")
    _add_task_selectors(p_eval)
    p_eval.add_argument("--validate-only", action="store_true")
    p_eval.add_argument("--dry-run", action="store_true")
    p_eval.set_defaults(func=cmd_evaluate)

    p_review = sub.add_parser("review", help="record semantic reviews for one task")
    p_review.add_argument("--task", required=True)
    p_review.add_argument("--reviews", required=True, help="JSON file with a review list")
    p_review.set_defaults(func=cmd_review)

    p_human = sub.add_parser("human", help="record the human decision for one task")
    p_human.add_argument("--task", required=True)
    p_human.add_argument("--decision", required=True, choices=["accept", "revise", "reject"])
    p_human.add_argument("--reviewer", required=True)
    p_human.add_argument("--reason", required=True)
    p_human.add_argument("--release-membership", help="comma separated release ids")
    p_human.set_defaults(func=cmd_human)

    p_freeze = sub.add_parser("freeze", help="freeze human-accepted tasks and select a release")
    _add_task_selectors(p_freeze)
    p_freeze.add_argument("--release", default="v1")
    p_freeze.add_argument("--dry-run", action="store_true")
    p_freeze.set_defaults(func=cmd_freeze)

    p_gen = sub.add_parser("generate", help="generate missing cases for one branch via API")
    p_gen.add_argument("--branch", required=True)
    p_gen.add_argument("--count", type=int, default=1)
    p_gen.add_argument("--allow-live-api", action="store_true")
    p_gen.add_argument("--model", default="gpt-4o-mini")
    p_gen.add_argument("--reasoning-effort")
    p_gen.add_argument("--seed", type=int)
    p_gen.add_argument("--retry-count", type=int, default=1)
    p_gen.set_defaults(func=cmd_generate)

    p_status = sub.add_parser("status", help="show registry status")
    p_status.add_argument("--task")
    p_status.set_defaults(func=cmd_status)
    return parser


def _add_task_selectors(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--all", action="store_true", help="every task in the registry")
    parser.add_argument("--task", help="one task id")
    parser.add_argument("--branch", help="one branch id")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        _json_out({"status": "PIPELINE_FAILED", "error_type": type(exc).__name__, "error": str(exc)}, stream=sys.stderr)
        raise SystemExit(1)
