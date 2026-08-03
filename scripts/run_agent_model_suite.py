#!/usr/bin/env python
"""Run the v2 eight-category Agent Model Safety Evaluation suite.

Run purposes:
  - dev: offline deterministic orchestration checks; never a formal score.
  - smoke: one-case-per-category checks; can be offline or explicitly live.
  - formal: live paired baseline/risk/recovery runs only.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.agent_model import CaseLoader, AgentModelJudge
from src.evaluation.agent_model.formal_guard import (
    FormalRunConfig,
    validate_formal_coverage,
    validate_formal_run,
)
from src.evaluation.agent_model.models import (
    CATEGORY_TO_RISK,
    PairedRunResult,
    ThreeLayerResult,
)
from src.evaluation.agent_model.suite_runner import AgentModelSuiteRunner
from src.evaluation.agent_model.dataset_split import split_cases
from src.evaluation.agent_model.run_manifest import (
    assert_manifests_compatible,
    build_manifest,
    sha256_bytes,
)
from src.evaluation.agent_model.scheduler import planned_case_order
from src.evaluation.agent_model.scheduler import assert_provider_version_stable
from src.evaluation.agent_model.statistics import category_summaries
from src.evaluation.agent_model.controls import run_control_checks
from src.evaluation.agent_model.trace_export import export_execution_trace
from src.experiment.runner import IoAEnvironment

DATASET = PROJECT_ROOT / "data" / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
CASE_DIR = PROJECT_ROOT / "data" / "agent_model_cases"
SMOKE_CASE_IDS = [
    "CAS-01", "CON-01", "RUM-01", "NOR-01",
    "INC-01", "JUD-01", "DIS-01", "AGE-01",
]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agent Model Safety Evaluation v2")
    parser.add_argument(
        "--run-purpose",
        choices=["dev", "smoke", "formal"],
        help=(
            "Run intent. dev defaults to offline deterministic; smoke runs one "
            "case per category; formal requires live paired all-variant runs."
        ),
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Deprecated alias for --run-purpose smoke",
    )
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--risk", default="all", choices=["all", *CATEGORY_TO_RISK.values()])
    parser.add_argument(
        "--cases",
        default="all",
        choices=["all"],
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--case-id")
    parser.add_argument(
        "--variant", default="all",
        choices=["baseline", "risk", "recovery", "all"],
    )
    parser.add_argument(
        "--skip-recovery",
        action="store_true",
        help=(
            "Development-only two-stage run: execute paired baseline and risk "
            "arms, and intentionally omit recovery."
        ),
    )
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--repeat-count", type=int)
    parser.add_argument(
        "--experiment-level",
        choices=["key_node", "ecosystem", "both"],
        default="both",
    )
    parser.add_argument("--resume-run-id")
    parser.add_argument("--order-seed", type=int, default=20260722)
    parser.add_argument(
        "--judge-calibration-report",
        help="JSON report proving blinded, independent Judge calibration for formal runs",
    )
    parser.add_argument("--output", default="results/agent_model")
    parser.add_argument(
        "--execution-mode",
        choices=["agentic_live", "offline_deterministic"],
        help=(
            "Execution backend. Defaults to offline_deterministic for dev/smoke "
            "and agentic_live for formal."
        ),
    )
    parser.add_argument(
        "--offline-fake-model", action="store_true",
        help=(
            "Deprecated alias for --execution-mode offline_deterministic; "
            "results are not formal scores"
        ),
    )
    return parser


def normalize_run_options(args: argparse.Namespace) -> argparse.Namespace:
    """Resolve deprecated aliases and enforce formal-run invariants."""
    if args.validate_only:
        args.run_purpose = args.run_purpose or "dev"
        args.execution_mode = args.execution_mode or "offline_deterministic"
        return args

    if args.smoke and args.run_purpose not in (None, "smoke"):
        raise SystemExit("--smoke cannot be combined with --run-purpose other than smoke")
    if args.smoke:
        args.run_purpose = "smoke"
    if args.run_purpose is None:
        args.run_purpose = "dev"

    if args.offline_fake_model:
        if args.execution_mode == "agentic_live":
            raise SystemExit(
                "--offline-fake-model conflicts with --execution-mode agentic_live"
            )
        args.execution_mode = "offline_deterministic"

    if args.execution_mode is None:
        args.execution_mode = (
            "agentic_live" if args.run_purpose == "formal"
            else "offline_deterministic"
        )

    if args.skip_recovery and args.variant != "all":
        raise SystemExit("--skip-recovery cannot be combined with --variant")

    if args.run_purpose == "formal":
        if args.execution_mode != "agentic_live":
            raise SystemExit("--run-purpose formal requires --execution-mode agentic_live")
        if args.offline_fake_model:
            raise SystemExit("--run-purpose formal cannot use --offline-fake-model")
        if args.variant != "all":
            raise SystemExit("--run-purpose formal requires --variant all")
        if args.skip_recovery:
            raise SystemExit("--run-purpose formal requires the recovery stage")
        if args.experiment_level != "both":
            raise SystemExit("--run-purpose formal requires --experiment-level both")
        if args.case_id:
            raise SystemExit("--run-purpose formal cannot select a single case")
        if args.risk != "all":
            raise SystemExit("--run-purpose formal requires all eight categories")
        if args.max_cases is not None:
            raise SystemExit("--run-purpose formal cannot truncate the case set")
        if args.repeat_count is not None:
            raise SystemExit(
                "--run-purpose formal uses each case's pre-registered repeat count"
            )

    return args


def resolve_variants(args: argparse.Namespace) -> list[str]:
    """Return the stages selected for this suite run."""
    if getattr(args, "skip_recovery", False):
        return ["baseline", "risk"]
    if args.variant == "all":
        return ["baseline", "risk", "recovery"]
    return [args.variant]


def select_cases(args: argparse.Namespace, cases: dict[str, Any]) -> list[Any]:
    if args.case_id:
        case = cases.get(args.case_id)
        selected = [case] if case is not None else []
    elif args.risk != "all":
        selected = [case for case in cases.values() if case.risk_type == args.risk]
    elif getattr(args, "run_purpose", None) == "smoke" or args.smoke:
        selected = [cases[case_id] for case_id in SMOKE_CASE_IDS if case_id in cases]
    else:
        selected = list(cases.values())
    if args.max_cases is not None:
        selected = selected[: args.max_cases]
    return selected


def _validate_positive_args(args: argparse.Namespace) -> None:
    for name in ("max_cases", "repeat_count"):
        value = getattr(args, name)
        if value is not None and value <= 0:
            raise SystemExit(f"--{name.replace('_', '-')} must be greater than zero")


def _has_llm_key() -> bool:
    if any(os.getenv(name) for name in (
        "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY",
    )):
        return True
    try:
        from src.llm.config import get_agent_llm_config
        get_agent_llm_config().get_api_key()
        return True
    except Exception:
        return False


async def build_environment(offline: bool) -> IoAEnvironment:
    if offline:
        config = {
            "execution_mode": "offline_deterministic",
            "offline_deterministic": True,
            "create_agent_runtimes": False,
            "enable_live_attack_injector": False,
            "enable_live_decision_agents": False,
            "enable_live_judges": False,
            "enable_safety_judge": False,
            "auto_bind_deterministic_runtimes": True,
        }
    else:
        config = {
            "execution_mode": "agentic_live",
            "agent_model_structured_output": True,
            # Internal task-specification, routing, and synthesis models are
            # outside the tested construct. Controlled steps use fixed specs,
            # deterministic registry routing, and the tested Agent's output.
            "enable_live_decision_agents": False,
            "enable_live_judges": False,
            "enable_live_attack_injector": False,
            "simulate_human_checkpoints": True,
        }
    environment = IoAEnvironment(config)
    for sub_ioa_id in ("finance", "healthcare", "travel", "news"):
        environment.add_sub_ioa(sub_ioa_id)
    await environment.setup_default_agents()
    await environment.setup_default_topology("full_mesh")
    return environment


def save_results(
    results: list[ThreeLayerResult],
    paired_results: list[PairedRunResult],
    path: Path,
    suite_run_id: str,
    *,
    run_purpose: str,
    execution_mode: str,
    variants: list[str],
    run_manifest: dict[str, Any],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    offline = execution_mode == "offline_deterministic"
    con_levels = {
        result.experiment_level for result in results
        if result.risk_type == CATEGORY_TO_RISK["CON"]
    }
    con_complete = not any(
        result.risk_type == CATEGORY_TO_RISK["CON"] for result in results
    ) or con_levels == {"key_node", "ecosystem"}
    coverage_errors = validate_formal_coverage(
        results, paired_results, run_manifest
    ) if run_purpose == "formal" else []
    runtime_integrity_errors = list(
        run_manifest.get("runtime_integrity_errors", [])
    )
    formal_eligible = (
        run_purpose == "formal"
        and not offline
        and variants == ["baseline", "risk", "recovery"]
        and bool(paired_results)
        and all(r.status != "INVALID" for r in results)
        and all(pair.formal_aggregate_eligible for pair in paired_results)
        and con_complete
        and not coverage_errors
        and not runtime_integrity_errors
    )
    watermark = sha256_bytes(
        f"{suite_run_id}:{run_manifest.get('manifest_hash', '')}:formal={formal_eligible}".encode()
    )
    payload = {
        "schema_version": "2.0",
        "suite_run_id": suite_run_id,
        "run_purpose": run_purpose,
        "execution_mode": execution_mode,
        "variants": variants,
        "recovery_executed": "recovery" in variants,
        "generated_at": datetime.now().isoformat(),
        "formal_score_eligible": formal_eligible,
        "formal_watermark": watermark if formal_eligible else None,
        "formal_ineligibility": [] if formal_eligible else (
            coverage_errors
            + runtime_integrity_errors
            + ([
                "development run, invalid result, paired gate failure, or incomplete consensus levels"
            ] if not coverage_errors and not runtime_integrity_errors else [])
        ),
        "offline_fake_model": offline,
        "total": len(results),
        "run_aborted": run_manifest.get("run_aborted"),
        "planned_result_total": run_manifest.get("planned_result_total"),
        "completed_result_total": run_manifest.get("completed_result_total"),
        "missing_result_total": run_manifest.get("missing_result_total"),
        "paired_unit_total": len(paired_results),
        "paired_unit_eligible": sum(
            pair.formal_aggregate_eligible for pair in paired_results
        ),
        "run_manifest": run_manifest,
        "trace_files": run_manifest.get("trace_exports", {}).get("files", {}),
        "category_summaries": category_summaries(results, paired_results),
        "paired_results": [pair.model_dump(mode="json") for pair in paired_results],
        "results": [result.model_dump(mode="json") for result in results],
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    (path.parent / "run_manifest.json").write_text(
        json.dumps(run_manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def build_run_manifest(
    args: argparse.Namespace,
    suite_run_id: str,
    planned_order: list[str] | None = None,
) -> dict[str, Any]:
    cases = CaseLoader(DATASET).load_all()
    selected_variants = resolve_variants(args)
    resolved_config = {
        "execution_mode": args.execution_mode,
        "run_purpose": args.run_purpose,
        "variant": args.variant,
        "selected_variants": selected_variants,
        "recovery_executed": "recovery" in selected_variants,
        "repeat_count": args.repeat_count,
        "experiment_level": getattr(args, "experiment_level", "both"),
        "provider_seed": None,
        "order_seed": getattr(args, "order_seed", 20260722),
    }
    manifest = build_manifest(
        PROJECT_ROOT,
        DATASET,
        suite_run_id,
        resolved_config,
        planned_order or sorted(cases),
        split_cases(cases),
    )
    manifest.update(_model_identity_manifest())
    selected_case_ids = [
        case_id for case_id in (planned_order or sorted(cases))
        if case_id in cases
    ]
    manifest["tested_model_request_config_by_case"] = {
        case_id: {
            "temperature": cases[case_id].tested_model_config.temperature,
            "top_p": cases[case_id].tested_model_config.top_p,
            "max_completion_tokens": (
                cases[case_id].tested_model_config.max_output_tokens
            ),
            "timeout": (
                cases[case_id].execution_config.request_timeout_seconds
            ),
            "retry_count": (
                cases[case_id].execution_config.max_api_retries + 1
            ),
            "retry_delay": (
                cases[case_id].execution_config.retry_backoff_seconds
            ),
        }
        for case_id in selected_case_ids
    }
    manifest["model_config_hash"] = _json_hash({
        "resolved_execution_config": resolved_config,
        "tested_model_identity": manifest["tested_model_identity"],
        "judge_model_identity": manifest["judge_model_identity"],
        "tested_model_request_config_by_case": manifest[
            "tested_model_request_config_by_case"
        ],
    })
    formal_case_ids = list(manifest["dataset_split"]["formal_evaluation"])
    formal_order = [
        case_id for case_id in (planned_order or []) if case_id in formal_case_ids
    ]
    formal_plan = {
        "case_ids": formal_order,
        "repeat_count_by_case": {
            case_id: cases[case_id].execution_config.repeat_count
            for case_id in formal_order
        },
        "experiment_levels_by_case": {
            case_id: (
                ["key_node", "ecosystem"]
                if cases[case_id].category_code == "CON"
                else ["key_node"]
            )
            for case_id in formal_order
        },
        "variants": ["baseline", "risk", "recovery"],
    }
    manifest["formal_plan"] = formal_plan
    manifest["formal_plan_hash"] = _json_hash(formal_plan)
    report_path = getattr(args, "judge_calibration_report", None)
    if report_path:
        manifest["judge_calibration"] = json.loads(
            Path(report_path).read_text(encoding="utf-8")
        )
    else:
        manifest["judge_calibration"] = {"calibrated": False}
    manifest["control_results"] = run_control_checks()
    return manifest


def _model_identity_manifest() -> dict[str, Any]:
    """Record public model identities and sampling settings without secrets."""
    from src.llm.config import get_agent_llm_config, get_judge_llm_config

    def identity(
        config: Any,
        *,
        effective_token_field: str,
        effective_temperature_field: str,
    ) -> dict[str, Any]:
        endpoint = str(getattr(config, "base_url", "") or "provider-default")
        return {
            "provider": str(getattr(config, "provider", "")),
            "model": str(getattr(config, "model", "")),
            "endpoint_hash": hashlib.sha256(endpoint.encode()).hexdigest(),
            "temperature": float(
                getattr(config, effective_temperature_field, 0.0)
            ),
            "max_completion_tokens": int(
                getattr(config, effective_token_field, 0)
            ),
            "context_window_tokens": int(
                getattr(config, "context_window_tokens", 0)
            ),
            "model_max_completion_tokens": int(
                getattr(config, "model_max_completion_tokens", 0)
            ),
        }

    tested_identity = identity(
            get_agent_llm_config(),
            effective_token_field="max_completion_tokens",
            effective_temperature_field="temperature",
        )
    tested_identity["sampling_settings_scope"] = (
        "default_only; actual case requests are recorded separately"
    )
    judge_identity = identity(
            get_judge_llm_config(),
            effective_token_field="judge_max_completion_tokens",
            effective_temperature_field="judge_temperature",
        )
    judge_identity["sampling_settings_scope"] = "effective_judge_request"
    return {
        "tested_model_identity": tested_identity,
        "judge_model_identity": judge_identity,
    }


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree_manifest_hash(paths: list[Path]) -> str:
    h = hashlib.sha256()
    for path in paths:
        if path.is_file():
            h.update(str(path.relative_to(PROJECT_ROOT)).encode("utf-8"))
            h.update(_file_sha256(path).encode("utf-8"))
        elif path.is_dir():
            for child in sorted(path.rglob("*.py")):
                h.update(str(child.relative_to(PROJECT_ROOT)).encode("utf-8"))
                h.update(_file_sha256(child).encode("utf-8"))
    return h.hexdigest()


def _json_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _paired_gates_passed(result: ThreeLayerResult) -> bool:
    gates = result.judge_verdict.get("paired_gates", {})
    if not gates:
        return False
    return all(gates.get(name) is True for name in (
        "baseline_gate",
        "risk_injection_gate",
        "evidence_completeness_gate",
        "judge_gate",
        "recovery_state_gate",
        "binding_and_config_gate",
    ))


async def run(args: argparse.Namespace) -> int:
    args = normalize_run_options(args)
    _validate_positive_args(args)
    loader = CaseLoader(DATASET)
    if args.validate_only:
        report = loader.validate_only()
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0 if report["errors"] == 0 else 1

    cases = loader.load_all()
    if loader.errors:
        print(f"ERROR: dataset has {len(loader.errors)} validation errors", file=sys.stderr)
        return 1
    selected = select_cases(args, cases)
    split = split_cases(cases)
    if args.run_purpose == "formal" and not args.case_id and args.risk == "all":
        selected = [
            case for case in selected
            if case.case_id in set(split["formal_evaluation"])
        ]
    selected = planned_case_order(selected, args.order_seed)
    if not selected:
        print("ERROR: no matching cases found", file=sys.stderr)
        return 2

    offline = args.execution_mode == "offline_deterministic"
    if not offline and not _has_llm_key():
        print("ERROR: no live model API key configured", file=sys.stderr)
        return 2
    if offline:
        print(
            "Offline deterministic mode: orchestration/path testing only; "
            "no formal score is produced."
        )
    else:
        print(
            f"Live mode ({args.run_purpose}): tested-model execution and the "
            "independent v2 semantic Judge are enabled."
        )

    suite_run_id = args.resume_run_id or f"agent-model-{uuid.uuid4().hex[:12]}"
    run_manifest = build_run_manifest(
        args, suite_run_id, [case.case_id for case in selected]
    )
    output_root = (PROJECT_ROOT / args.output).resolve()
    output_path = output_root / suite_run_id / "run_results.json"
    db_path = output_root / suite_run_id / "context.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if args.resume_run_id:
        previous_manifest_path = output_path.parent / "run_manifest.json"
        if not previous_manifest_path.exists():
            print(
                "ERROR: resume requires the original run_manifest.json",
                file=sys.stderr,
            )
            return 2
        previous_manifest = json.loads(
            previous_manifest_path.read_text(encoding="utf-8")
        )
        try:
            assert_manifests_compatible([previous_manifest, run_manifest])
        except ValueError as exc:
            print(f"ERROR: incompatible resume manifest: {exc}", file=sys.stderr)
            return 2
    environment = await build_environment(offline)
    judge_callback = None
    if not offline:
        try:
            from src.llm.client import get_judge_llm_client
            judge_callback = AgentModelJudge(get_judge_llm_client())
        except Exception as exc:
            print(
                f"WARNING: v2 Judge unavailable ({exc}); results will remain INVALID.",
                file=sys.stderr,
            )
    variants = resolve_variants(args)
    try:
        validate_formal_run(
            FormalRunConfig(
                run_purpose=args.run_purpose,
                execution_mode=args.execution_mode,
                variants=variants,
                judge_configured=judge_callback is not None,
                fake_model=offline,
                manifest=run_manifest,
            )
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    runner = AgentModelSuiteRunner(
        case_dir=CASE_DIR,
        db_path=db_path,
        environment=environment,
        fake_model=offline,
        judge_callback=judge_callback,
        suite_run_id=suite_run_id,
        resume=bool(args.resume_run_id),
        run_purpose=args.run_purpose,
        execution_mode=args.execution_mode,
        run_manifest=run_manifest,
        experiment_level="key_node",
    )
    await runner.open()
    run_aborted: dict[str, Any] | None = None
    try:
        results: list[ThreeLayerResult] = []
        for case in selected:
            levels = (
                ["key_node", "ecosystem"]
                if case.category_code == "CON" and args.experiment_level == "both"
                else [
                    args.experiment_level
                    if args.experiment_level != "both" else "key_node"
                ]
            )
            for level in levels:
                runner.experiment_level = level
                case_results = await runner.run_case(
                    case,
                    variants=variants,
                    repeat_count=args.repeat_count,
                )
                results.extend(case_results)
                invalid_result = next(
                    (
                        result for result in case_results
                        if result.status == "INVALID"
                    ),
                    None,
                )
                if invalid_result is not None and not offline:
                    run_aborted = {
                        "case_id": invalid_result.case_id,
                        "variant": invalid_result.variant,
                        "run_id": invalid_result.run_id,
                        "failure_code": (
                            invalid_result.model_behavior.get("failure_code")
                            or invalid_result.judge_verdict.get("status")
                            or "INVALID"
                        ),
                        "reason": (
                            invalid_result.model_behavior.get("error")
                            or invalid_result.judge_verdict.get("reason")
                            or "The live evaluation stage was invalid"
                        ),
                    }
                    break
            if run_aborted is not None:
                break
    finally:
        await runner.close()

    planned_result_total = sum(
        (
            2
            if case.category_code == "CON" and args.experiment_level == "both"
            else 1
        )
        * len(variants)
        * args.repeat_count
        for case in selected
    )
    run_outcome = {
        "run_aborted": run_aborted,
        "planned_result_total": planned_result_total,
        "completed_result_total": len(results),
        "missing_result_total": max(0, planned_result_total - len(results)),
    }
    run_manifest.update(run_outcome)
    run_manifest["actual_order"] = [
        {
            "run_id": result.run_id,
            "case_id": result.case_id,
            "variant": result.variant,
            "experiment_level": result.experiment_level,
        }
        for result in results
    ]
    trace_export = export_execution_trace(
        db_path,
        output_path.parent,
        suite_run_id=suite_run_id,
        run_outcome=run_outcome,
    )
    run_manifest["trace_exports"] = {
        "record_count": trace_export["record_count"],
        "run_result_count": trace_export["run_result_count"],
        "scenario_snapshot_count": trace_export["scenario_snapshot_count"],
        "standalone_event_count": trace_export["standalone_event_count"],
        "files": trace_export["files"],
        "complete_record_files": trace_export["complete_record_files"],
        "process_record_files": trace_export["process_record_files"],
        "readable_category_files": trace_export["readable_category_files"],
    }
    usage = trace_export["usage"]
    run_manifest["usage"] = {
        "input_tokens": usage["prompt_tokens"],
        "output_tokens": usage["completion_tokens"],
        "total_tokens": usage["total_tokens"],
        "cost": None,
    }
    run_manifest["runtime"] = trace_export["runtime"]
    judge_runtime = list(
        getattr(judge_callback, "runtime_records", []) if judge_callback else []
    )
    run_manifest["judge_runtime"] = judge_runtime
    runtime_integrity_errors: list[str] = []
    if args.run_purpose == "formal":
        observed_tested_versions = list(
            trace_export["runtime"].get("observed_models", [])
        )
        observed_judge_versions = sorted({
            str(item.get("provider_metadata", {}).get("model", ""))
            for item in judge_runtime
            if item.get("provider_metadata", {}).get("model")
        })
        if not observed_tested_versions:
            runtime_integrity_errors.append(
                "tested-model provider version was not recorded"
            )
        if not observed_judge_versions:
            runtime_integrity_errors.append(
                "Judge provider version was not recorded"
            )
        for label, versions in (
            ("tested model", observed_tested_versions),
            ("Judge", observed_judge_versions),
        ):
            try:
                assert_provider_version_stable(versions)
            except RuntimeError as exc:
                runtime_integrity_errors.append(f"{label}: {exc}")
        run_manifest["observed_tested_model_versions"] = observed_tested_versions
        run_manifest["observed_judge_model_versions"] = observed_judge_versions
    run_manifest["runtime_integrity_errors"] = runtime_integrity_errors
    run_manifest["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    run_manifest["manifest_hash"] = sha256_bytes(
        json.dumps(run_manifest, ensure_ascii=False, sort_keys=True).encode()
    )

    save_results(
        results,
        runner._paired_results,
        output_path,
        suite_run_id,
        run_purpose=args.run_purpose,
        execution_mode=args.execution_mode,
        variants=variants,
        run_manifest=run_manifest,
    )
    print(json.dumps({
        "suite_run_id": suite_run_id,
        "results": len(results),
        "output": str(output_path),
        "complete_trace_jsonl": str(output_path.parent / "execution_trace.jsonl"),
        "complete_trace_markdown": str(output_path.parent / "execution_trace.md"),
        "complete_trace_html": str(output_path.parent / "execution_trace.html"),
        "readable_trace": str(output_path.parent / "execution_trace.md"),
        "visual_trace": str(output_path.parent / "execution_trace.html"),
        "category_process_records": {
            code: str(output_path.parent / relative_path)
            for code, relative_path in trace_export["process_record_files"].items()
        },
        "run_aborted": run_aborted,
    }, ensure_ascii=False))
    return 1 if run_aborted is not None else 0


async def main() -> None:
    raise SystemExit(await run(build_parser().parse_args()))


if __name__ == "__main__":
    asyncio.run(main())
