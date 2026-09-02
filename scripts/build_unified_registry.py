#!/usr/bin/env python
"""Inspect the canonical single Registry and optionally resume its tasks."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.scenario_generation.unified_architecture import PipelineOrchestrator  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "data" / "unified_cases")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    orchestrator = PipelineOrchestrator(args.root.expanduser().resolve())
    task_ids = sorted(orchestrator.registry.data.entries)
    if args.resume:
        for task_id in task_ids:
            orchestrator.resume(task_id)
    stages = Counter(orchestrator.registry.get(task_id).stage for task_id in task_ids)
    print(json.dumps({"status": "REGISTRY_READY", "registry": str(orchestrator.registry.path), "task_count": len(task_ids), "stage_counts": dict(sorted(stages.items()))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
