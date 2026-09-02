#!/usr/bin/env python
"""Validate one AI-generated ten-item candidate batch without model calls."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pydantic import ValidationError  # noqa: E402

from src.evaluation.scenario_generation import ScenarioGenerationBatch  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate a generated ten-item scenario batch."
    )
    parser.add_argument("input", type=Path)
    args = parser.parse_args()

    try:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
        batch = ScenarioGenerationBatch.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        print(json.dumps({
            "status": "INVALID",
            "input": str(args.input),
            "error": str(exc),
            "provider_calls": 0,
        }, ensure_ascii=False, indent=2))
        return 1

    print(json.dumps({
        "status": "VALID",
        "input": str(args.input),
        "prompt_version": batch.prompt_version,
        "generation_status": batch.generation_status,
        "target_category": batch.generation_config.target_category,
        "case_count": len(batch.cases),
        "provider_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
