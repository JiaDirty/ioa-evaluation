#!/usr/bin/env python
"""Canonical compatibility command using ScenarioTask and one Registry."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.migrations.migrate_scenario_tasks_v1 import _candidate_tasks, _historical_tasks  # noqa: E402
from src.evaluation.scenario_generation.orchestrator import PipelineOrchestrator  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "data" / "candidate_batches" / "批量生成-第01轮")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "unified_cases")
    parser.add_argument("--source-kind", choices=("historical", "candidate"), default="candidate")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    source = args.source.expanduser().resolve()
    tasks = (_historical_tasks(source) if args.source_kind == "historical" else _candidate_tasks(source))
    if args.limit is not None:
        tasks = tasks[: args.limit]
    orchestrator = PipelineOrchestrator(args.output.expanduser().resolve())
    for task in tasks:
        orchestrator.submit(task)
        orchestrator.process(task.task_id)
    print(json.dumps({"status": "CANONICAL_PIPELINE_COMPLETED", "task_count": len(tasks), "registry": str(orchestrator.registry.path)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
