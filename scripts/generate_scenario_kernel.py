#!/usr/bin/env python
"""Opt-in AI Hub Mix generation of one ScenarioKernel."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.scenario_generation.pipeline_api import (  # noqa: E402
    PipelineAPI,
    StageCallConfig,
)


def _json_or_file(value: str) -> dict:
    path = Path(value)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return json.loads(value)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--task-card", required=True, help="JSON 字符串或 JSON 文件")
    ap.add_argument("--prompt", required=True, type=Path)
    ap.add_argument("--candidate-uid", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--output", required=True, type=Path)
    ap.add_argument("--source-case-id")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--reasoning-effort")
    ap.add_argument("--temperature", type=float, default=0.4)
    ap.add_argument("--max-completion-tokens", type=int, default=16384)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--retry-count", type=int, default=1)
    ap.add_argument("--allow-live-api", action="store_true")
    args = ap.parse_args(argv)
    try:
        kernel = PipelineAPI().generate_kernel(
            task_card=_json_or_file(args.task_card),
            prompt=args.prompt.read_text(encoding="utf-8"),
            candidate_uid=args.candidate_uid,
            source_case_id=args.source_case_id,
            config=StageCallConfig(
                model_id=args.model,
                reasoning_effort=args.reasoning_effort,
                seed=args.seed,
                temperature=args.temperature,
                max_completion_tokens=args.max_completion_tokens,
                timeout=args.timeout,
                retry_count=args.retry_count,
            ),
            output_dir=args.output,
            allow_live_api=args.allow_live_api,
        )
        print(json.dumps({"status": "KERNEL_VALID", "kernel_id": kernel.kernel_id, "path": str((args.output / "kernel.json").resolve())}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__, "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
