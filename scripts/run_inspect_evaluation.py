#!/usr/bin/env python
"""Run the IOA paired evaluation through Inspect AI."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from inspect_ai import eval as inspect_eval
from inspect_ai.model import GenerateConfig, get_model

from src.evaluation.business_protocol.dataset import load_evaluation_dataset
from src.evaluation.inspect_adapter import build_inspect_samples, build_inspect_task


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("value must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run one paired IOA scenario per Inspect AI sample",
    )
    parser.add_argument(
        "--data",
        action="append",
        default=None,
        help="Scenario JSON/JSONL file or directory; repeat for multiple sources.",
    )
    parser.add_argument(
        "--dataset-profile",
        choices=["reference_source", "generic_expandable", "mixed"],
        default="reference_source",
    )
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--case-id", action="append", default=None)
    parser.add_argument(
        "--run-level",
        choices=["key_node", "full_chain"],
        default="full_chain",
    )
    parser.add_argument(
        "--execution-mode",
        choices=["offline-scripted", "agentic-live"],
        default="offline-scripted",
    )
    parser.add_argument("--allow-live-api", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--model", help="AI Hub Mix model alias; defaults to agent config.")
    parser.add_argument("--epochs", type=_positive_int, default=1)
    parser.add_argument("--max-connections", type=_positive_int, default=8)
    parser.add_argument("--retry-on-error", type=int, default=1)
    parser.add_argument("--log-dir", default=".local/results/inspect_ai")
    parser.add_argument("--display", choices=["none", "full"], default="full")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    sources = args.data or [str(PROJECT_ROOT / "data" / "scenarios")]
    dataset = load_evaluation_dataset(
        sources,
        profile=args.dataset_profile,
        recursive=args.recursive,
        require_complete_reference=(args.dataset_profile == "reference_source" and not args.data),
    )
    samples = build_inspect_samples(dataset, case_ids=args.case_id)
    if args.validate_only:
        print(json.dumps({
            "status": "VALID",
            "adapter": "ioa_inspect_adapter_v1",
            **dataset.report.as_dict(),
            "selected_sample_count": len(samples),
            "sample_granularity": "one_complete_paired_scenario",
            "run_level": args.run_level,
            "provider_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 0

    if args.execution_mode == "agentic-live" and not args.allow_live_api:
        raise SystemExit(
            "agentic-live may incur API charges; add --allow-live-api after validation"
        )
    execution_mode = (
        "inspect-provider" if args.execution_mode == "agentic-live" else "offline-scripted"
    )
    task = build_inspect_task(
        dataset,
        case_ids=args.case_id,
        run_level=args.run_level,
        execution_mode=execution_mode,
    )

    eval_kwargs = {
        "tasks": task,
        "log_dir": args.log_dir,
        "epochs": args.epochs,
        "max_connections": args.max_connections,
        "retry_on_error": max(0, args.retry_on_error),
        "fail_on_error": False,
        "log_model_api": True,
        "display": args.display,
    }
    if args.execution_mode == "agentic-live":
        from src.llm.config import get_agent_llm_config

        config = get_agent_llm_config()
        model_alias = args.model or config.model
        eval_kwargs.update({
            "model": get_model(
                f"openai/{model_alias}",
                base_url=config.base_url,
                api_key=config.get_api_key(),
                config=GenerateConfig(
                    temperature=config.temperature,
                    top_p=config.top_p,
                    max_tokens=config.max_completion_tokens,
                    max_retries=max(0, config.retry_count - 1),
                    timeout=config.timeout,
                    max_connections=args.max_connections,
                ),
            ),
        })
    else:
        eval_kwargs["model"] = "mockllm/model"

    logs = inspect_eval(**eval_kwargs)
    print(json.dumps({
        "status": "COMPLETED" if all(log.status == "success" for log in logs) else "PARTIAL",
        "execution_mode": args.execution_mode,
        "sample_count": len(samples),
        "epochs": args.epochs,
        "log_paths": [log.location for log in logs],
        "log_statuses": [log.status for log in logs],
    }, ensure_ascii=False, indent=2))
    return 0 if all(log.status == "success" for log in logs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
