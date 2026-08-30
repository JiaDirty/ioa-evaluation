#!/usr/bin/env python
"""Validate or run the canonical neutral-business evaluation protocol.

No provider request is made unless both ``--execution-mode agentic-live`` and
``--allow-live-api`` are present.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.dataset import (
    DatasetCompatibilityError,
    DatasetValidationReport,
    load_evaluation_dataset,
)
from src.evaluation.business_protocol.loader import CaseDataLoadError
from src.evaluation.business_protocol.prompt import (
    AGENT_BUSINESS_RESULT_SCHEMA,
    COMMON_SYSTEM_MESSAGE,
    build_api_tools,
    build_user_message,
)
from src.evaluation.business_protocol.runner import BusinessProtocolRunner
from src.evaluation.business_protocol.scripted_client import ProtocolValidationClient
from src.evaluation.business_protocol.validation import validate_case_catalog


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def build_parser(
    *,
    default_dataset_profile: str = "legacy_reference",
) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Neutral business Agent evaluation dataset runner")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument(
        "--execution-mode",
        choices=["offline-scripted", "agentic-live"],
        default="offline-scripted",
    )
    parser.add_argument(
        "--allow-live-api",
        action="store_true",
        help="Required second switch for provider calls; may incur API charges.",
    )
    parser.add_argument("--case-id", default="all")
    parser.add_argument(
        "--dataset-profile",
        choices=["legacy_reference", "generic_expandable"],
        default=default_dataset_profile,
        help=(
            "legacy_reference accepts only the hash-locked original 11 cases; "
            "generic_expandable requires a generic_scoring_v1 contract on every case."
        ),
    )
    parser.add_argument(
        "--data",
        action="append",
        help="Scenario JSON/JSONL file or directory; repeat for multiple sources.",
    )
    parser.add_argument(
        "--data-dir",
        help="Compatibility alias for one scenario directory.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Discover supported scenario files recursively below supplied directories.",
    )
    parser.add_argument(
        "--condition",
        choices=["baseline", "mechanism", "recovery", "all"],
        default="all",
    )
    parser.add_argument(
        "--run-level",
        choices=["key_node", "full_chain", "both"],
        default="both",
    )
    parser.add_argument(
        "--output",
        default=".local/results/business_protocol",
    )
    parser.add_argument(
        "--repeat-count",
        type=_positive_int,
        default=1,
        help="Repeat every selected case and run level independently.",
    )
    parser.add_argument(
        "--export-prompts",
        help="Write every initial request snapshot as JSONL without calling a model.",
    )
    return parser


def _selected_cases(args: argparse.Namespace, cases: dict[str, Any]) -> list[Any]:
    if args.case_id == "all":
        return list(cases.values())
    if args.case_id not in cases:
        raise SystemExit(f"unknown --case-id: {args.case_id}")
    return [cases[args.case_id]]


def _conditions(value: str) -> list[str]:
    return ["baseline", "mechanism", "recovery"] if value == "all" else [value]


def _run_levels(value: str) -> list[str]:
    return ["key_node", "full_chain"] if value == "both" else [value]


def _load_dataset(
    args: argparse.Namespace,
    *,
    require_data: bool,
) -> tuple[dict[str, Any], DatasetValidationReport, list[str]]:
    if args.data and args.data_dir:
        raise SystemExit("use --data or --data-dir, not both")
    sources = list(args.data or [])
    if args.data_dir:
        sources.append(args.data_dir)
    if not sources:
        if require_data:
            raise SystemExit("the expandable dataset runner requires at least one --data source")
        if args.dataset_profile != "legacy_reference":
            raise SystemExit("generic_expandable requires at least one --data source")
        dataset = load_evaluation_dataset(
            [PROJECT_ROOT / "data" / "scenarios"],
            profile="legacy_reference",
            require_complete_legacy=True,
        )
        cases = dataset.cases
        report = dataset.report
        validate_case_catalog(cases)
        return cases, report, [str(path.resolve()) for path in dataset.source_files]

    dataset = load_evaluation_dataset(
        sources,
        profile=args.dataset_profile,
        recursive=args.recursive,
    )
    return (
        dataset.cases,
        dataset.report,
        [str(path.resolve()) for path in dataset.source_files],
    )


def export_prompts(path: Path, cases: list[Any], conditions: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            for condition in conditions:
                visible_steps = list(case.steps)
                if condition == "recovery":
                    visible_steps.extend(case.recovery_steps)
                for step in visible_steps:
                    handle.write(json.dumps({
                        "case_id": case.case_id,
                        "category": case.category,
                        "condition": condition,
                        "step_id": step.step_id,
                        "messages": [
                            {"role": "system", "content": COMMON_SYSTEM_MESSAGE},
                            {"role": "user", "content": build_user_message(step, condition)},
                        ],
                        "tools": build_api_tools(step.tools_for(condition)),
                        "tool_choice": "auto",
                        "parallel_tool_calls": False,
                        "response_format": AGENT_BUSINESS_RESULT_SCHEMA,
                    }, ensure_ascii=False) + "\n")
                    count += 1
    return count


def _build_live_client():
    from src.llm.client import OpenAIClient
    from src.llm.config import get_agent_llm_config

    return OpenAIClient(get_agent_llm_config())


async def async_main(args: argparse.Namespace, *, require_data: bool = False) -> int:
    cases, dataset_report, source_files = _load_dataset(args, require_data=require_data)
    selected = _selected_cases(args, cases)
    conditions = _conditions(args.condition)
    levels = _run_levels(args.run_level)

    exported = 0
    if args.export_prompts:
        exported = export_prompts(Path(args.export_prompts), selected, conditions)

    if args.validate_only:
        results_per_repeat = len(selected) * len(levels)
        if args.condition != "all":
            results_per_repeat *= len(conditions)
        print(json.dumps({
            "status": "VALID",
            **dataset_report.as_dict(),
            "selected_case_count": len(selected),
            "step_count": sum(len(case.steps) + len(case.recovery_steps) for case in selected),
            "source_file_count": len(source_files),
            "repeat_count": args.repeat_count,
            "planned_result_count": results_per_repeat * args.repeat_count,
            "prompt_snapshots_exported": exported,
            "provider_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.execution_mode == "agentic-live" and not args.allow_live_api:
        raise SystemExit(
            "agentic-live may incur API charges; add --allow-live-api only after reviewing exported prompts"
        )
    client = _build_live_client() if args.execution_mode == "agentic-live" else ProtocolValidationClient()
    runner = BusinessProtocolRunner(client)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = datetime.now(timezone.utc).strftime("business-%Y%m%dT%H%M%SZ")
    results = []
    result_repeat_indexes = []
    for repeat_index in range(1, args.repeat_count + 1):
        for case in selected:
            for level in levels:
                if args.condition == "all":
                    results.append(await runner.run_paired_case(case, run_level=level))
                    result_repeat_indexes.append(repeat_index)
                else:
                    for condition in conditions:
                        results.append(await runner.run_case(case, condition, run_level=level))
                        result_repeat_indexes.append(repeat_index)
    payload = {
        "run_id": run_id,
        "dataset": {
            **dataset_report.as_dict(),
            "source_files": source_files,
        },
        "execution_mode": args.execution_mode,
        "repeat_count": args.repeat_count,
        "result_repeat_indexes": result_repeat_indexes,
        "provider_calls": (
            runner.provider_call_count if args.execution_mode == "agentic-live" else 0
        ),
        "protocol_turns": runner.protocol_turn_count,
        "formal_score_eligible": False,
        "note": (
            "离线结果只验证协议和状态机，不是模型安全分数。"
            if args.execution_mode == "offline-scripted"
            else "单次代表性运行仍需独立 Judge、人工复核和 holdout 后才能形成正式结论。"
        ),
        "results": [result.model_dump(mode="json") for result in results],
    }
    result_path = output_dir / f"{run_id}.json"
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({
        "status": "COMPLETED",
        "dataset_profile": dataset_report.profile,
        "execution_mode": args.execution_mode,
        "result_count": len(results),
        "repeat_count": args.repeat_count,
        "result_path": str(result_path.resolve()),
        "provider_calls": payload["provider_calls"],
        "protocol_turns": payload["protocol_turns"],
    }, ensure_ascii=False, indent=2))
    return 0


def main(
    *,
    default_dataset_profile: str = "legacy_reference",
    require_data: bool = False,
) -> int:
    args = build_parser(default_dataset_profile=default_dataset_profile).parse_args()
    try:
        return asyncio.run(async_main(args, require_data=require_data))
    except (CaseDataLoadError, DatasetCompatibilityError) as exc:
        print(json.dumps({
            "status": "INVALID_DATASET",
            "error_type": type(exc).__name__,
            "error": str(exc),
            "provider_calls": 0,
        }, ensure_ascii=False, indent=2), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
