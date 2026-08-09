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

from src.evaluation.business_protocol.cases import get_business_cases
from src.evaluation.business_protocol.prompt import (
    AGENT_BUSINESS_RESULT_SCHEMA,
    COMMON_SYSTEM_MESSAGE,
    build_api_tools,
    build_user_message,
)
from src.evaluation.business_protocol.runner import BusinessProtocolRunner
from src.evaluation.business_protocol.scripted_client import ProtocolValidationClient
from src.evaluation.business_protocol.validation import validate_case_catalog


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Eight-category neutral business Agent evaluation")
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


def export_prompts(path: Path, cases: list[Any], conditions: list[str]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases:
            for condition in conditions:
                for step in case.steps:
                    handle.write(json.dumps({
                        "case_id": case.case_id,
                        "category": case.category,
                        "condition": condition,
                        "step_id": step.step_id,
                        "messages": [
                            {"role": "system", "content": COMMON_SYSTEM_MESSAGE},
                            {"role": "user", "content": build_user_message(step, condition)},
                        ],
                        "tools": build_api_tools(step.tools),
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


async def async_main(args: argparse.Namespace) -> int:
    cases = get_business_cases()
    validate_case_catalog(cases)
    selected = _selected_cases(args, cases)
    conditions = _conditions(args.condition)
    levels = _run_levels(args.run_level)

    exported = 0
    if args.export_prompts:
        exported = export_prompts(Path(args.export_prompts), selected, conditions)

    if args.validate_only:
        print(json.dumps({
            "status": "VALID",
            "case_count": len(selected),
            "step_count": sum(len(case.steps) for case in selected),
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
    for case in selected:
        for condition in conditions:
            for level in levels:
                results.append(await runner.run_case(case, condition, run_level=level))
    payload = {
        "run_id": run_id,
        "execution_mode": args.execution_mode,
        "provider_calls": (
            len(getattr(client, "last_provider_calls", []))
            if args.execution_mode == "agentic-live"
            else 0
        ),
        "protocol_turns": len(getattr(client, "calls", [])),
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
        "execution_mode": args.execution_mode,
        "result_count": len(results),
        "result_path": str(result_path.resolve()),
        "provider_calls": payload["provider_calls"],
        "protocol_turns": payload["protocol_turns"],
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    return asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
