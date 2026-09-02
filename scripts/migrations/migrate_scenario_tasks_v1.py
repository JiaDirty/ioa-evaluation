#!/usr/bin/env python
"""Convert historical and candidate cases into the single ScenarioTask format."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.business_protocol.loader import load_business_cases  # noqa: E402
from src.evaluation.candidate_review import discover_candidates  # noqa: E402
from src.evaluation.scenario_generation.orchestrator import (  # noqa: E402
    PipelineOrchestrator,
    ScenarioTask,
    TaskProvenance,
)
from scripts.migrations.reference_case_conversion import (  # noqa: E402
    convert_reference_case,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _portable_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"migration source must be inside project root: {path}") from exc


def _task_id(origin: str, source: Path, case_id: str) -> str:
    # Task identity must be reproducible across checkouts and machines; never
    # hash a host-specific absolute path into the durable ID.
    value = f"{origin}|{_portable_path(source)}|{case_id}"
    return "task-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _historical_tasks(source: Path) -> list[ScenarioTask]:
    tasks: list[ScenarioTask] = []
    for case_id, case in sorted(load_business_cases(source).items()):
        case = convert_reference_case(case)
        file_candidates = sorted(source.glob("*.jsonl"))
        source_path = next(
            (path for path in file_candidates if case_id in path.read_text(encoding="utf-8")),
            source / "README.md",
        )
        tasks.append(
            ScenarioTask.from_case(
                case,
                task_id=_task_id("historical", source_path, case_id),
                provenance=TaskProvenance(
                    origin="historical",
                    source_path=_portable_path(source_path),
                    source_sha256=_sha256(source_path),
                ),
            )
        )
    return tasks


def _candidate_tasks(source: Path) -> list[ScenarioTask]:
    tasks: list[ScenarioTask] = []
    for record in discover_candidates(source):
        metadata = record.case.metadata.get("generation_provenance", {})
        if not isinstance(metadata, dict):
            metadata = {}
        tasks.append(
            ScenarioTask.from_case(
                record.case,
                task_id=_task_id("candidate", record.source_path, record.candidate_uid),
                provenance=TaskProvenance(
                    origin="candidate",
                    source_path=_portable_path(record.source_path),
                    source_sha256=_sha256(record.source_path),
                    model_id=record.generator_model_id,
                    seed=metadata.get("generation_seed"),
                    prompt_version=metadata.get("prompt_version"),
                ),
                metadata={"candidate_uid": record.candidate_uid, "batch_id": record.batch_id},
            )
        )
    return tasks


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical", type=Path, default=ROOT / "data" / "scenarios")
    parser.add_argument("--candidates", type=Path, default=ROOT / "data" / "candidate_batches" / "批量生成-第01轮")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "unified_cases")
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args(argv)

    tasks = _historical_tasks(args.historical.expanduser().resolve()) + _candidate_tasks(args.candidates.expanduser().resolve())
    if args.limit is not None:
        tasks = tasks[: args.limit]
    orchestrator = PipelineOrchestrator(args.output.expanduser().resolve())
    for task in tasks:
        orchestrator.submit(task)
    summary = {
        "schema_version": "unified_task_migration_v1",
        "task_count": len(tasks),
        "historical_count": sum(task.provenance.origin == "historical" for task in tasks),
        "candidate_count": sum(task.provenance.origin == "candidate" for task in tasks),
        "registry": str(orchestrator.registry.path),
        "case_root": str(orchestrator.cases_root),
        "status": "TASKS_CREATED",
    }
    (orchestrator.root / "migration_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
