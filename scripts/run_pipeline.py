#!/usr/bin/env python
"""Operate the canonical ScenarioTask -> CompiledCase pipeline."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.scenario_generation.orchestrator import (  # noqa: E402
    PipelineOrchestrator,
    ScenarioTask,
)


def _task_paths(args: argparse.Namespace, root: Path) -> list[Path]:
    if args.task_file:
        return [args.task_file.expanduser().resolve()]
    if args.task_dir:
        directory = args.task_dir.expanduser().resolve()
        return sorted(directory.rglob("scenario_task.json"))
    if args.process_all:
        return sorted((root / "cases").rglob("scenario_task.json"))
    raise ValueError("请提供 --task-file、--task-dir 或 --process-all")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT / "data" / "unified_cases")
    parser.add_argument("--task-file", type=Path)
    parser.add_argument("--task-dir", type=Path)
    parser.add_argument("--task-id")
    parser.add_argument("--process-all", action="store_true")
    parser.add_argument("--process", action="store_true", help="提交后推进每条任务")
    parser.add_argument("--resume", action="store_true", help="从 Registry 当前状态续跑")
    args = parser.parse_args(argv)

    root = args.root.expanduser().resolve()
    orchestrator = PipelineOrchestrator(root)
    paths = _task_paths(args, root)
    if not paths and not args.task_id:
        raise ValueError("没有找到 scenario_task.json")

    task_ids: list[str] = []
    for path in paths:
        task = ScenarioTask.model_validate_json(path.read_text(encoding="utf-8"))
        orchestrator.submit(task)
        task_ids.append(task.task_id)
    if args.task_id:
        task_ids = [args.task_id]
    if args.process or args.resume:
        for task_id in task_ids:
            (orchestrator.resume if args.resume else orchestrator.process)(task_id)

    stages = Counter(orchestrator.registry.get(task_id).stage for task_id in task_ids)
    payload = {
        "status": "CANONICAL_PIPELINE_COMPLETED",
        "orchestrator_version": "scenario_orchestrator_v1",
        "registry": str(orchestrator.registry.path),
        "task_count": len(task_ids),
        "stage_counts": dict(sorted(stages.items())),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(
            json.dumps(
                {"status": "CANONICAL_PIPELINE_FAILED", "error_type": type(exc).__name__, "error": str(exc)},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1)
