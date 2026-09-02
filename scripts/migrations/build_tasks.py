#!/usr/bin/env python
"""Build lightweight ScenarioTask cards from raw sources.

This is the single import path for legacy material: the 11 reference cases and
the 440 generated candidates are converted into task cards whose only payload
is objective/constraints plus read-only ``reference_material``.  Original
files are never modified.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.loader import (  # noqa: E402
    load_business_cases_from_paths,
)
from src.evaluation.candidate_review.deterministic import discover_candidates  # noqa: E402
from src.evaluation.scenario_generation.catalog import load_evaluation_catalog  # noqa: E402
from src.evaluation.scenario_generation.models import (  # noqa: E402
    ReferenceMaterial,
    ScenarioTask,
    TaskProvenance,
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _task_id(origin: str, rel_path: str, case_id: str) -> str:
    value = f"{origin}|{rel_path}|{case_id}"
    return "task-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _task_id_uid(origin: str, rel_path: str, uid: str) -> str:
    value = f"{origin}|{rel_path}|{uid}"
    return "task-" + hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _mechanism_requirements(catalog: Any, branch_id: str) -> list[str]:
    branch = catalog.branch_for_id(branch_id)
    category = next(item for item in catalog.categories if item.code == branch.category)
    return [category.mechanism]


def _derive_subtype(case_id: str, category: str, declared: str | None) -> str | None:
    """Resolve the trust-authorization sub-mechanism when metadata omits it."""

    if category != "TRA":
        return None
    if declared in {"drift", "vague"}:
        return declared
    lowered = case_id.lower()
    if "drift" in lowered:
        return "drift"
    if "vague" in lowered:
        return "vague"
    # Candidate batches use several historical metadata keys for the same
    # branch.  Normalize their values here so branch selection remains a
    # catalog operation rather than a source-specific path.
    for value in (declared,):
        if not isinstance(value, str):
            continue
        lowered_value = value.lower()
        if "drift" in lowered_value or "转述" in value or "扩大" in value:
            return "drift"
        if "vague" in lowered_value or "ambig" in lowered_value or "含糊" in value or "模糊" in value:
            return "vague"
    return None


def _build_task(
    *,
    task_id: str,
    branch_id: str,
    case_id: str,
    title: str,
    purpose: str,
    origin: str,
    rel_path: str,
    source_sha256: str,
    model_id: str | None,
    seed: str | None,
    prompt_version: str | None,
    subtype: str | None,
    created_at: str,
) -> ScenarioTask:
    catalog = load_evaluation_catalog()
    branch = catalog.branch_for_id(branch_id)
    subtype = _derive_subtype(case_id, branch.category, subtype)
    branch = catalog.branch_for_case(branch.category, subtype)
    material = ReferenceMaterial(
        ref_id=f"src-{case_id}",
        kind="case_jsonl",
        source_path=rel_path,
        source_sha256=source_sha256,
        notes=[f"原始来源:{origin}; 只读, 不得修改"],
    )
    return ScenarioTask.create(
        task_id=task_id,
        branch_id=branch_id,
        subtype=subtype,
        objective=f"测量 {branch.name_zh} 风险机制: {catalog.branch_for_id(branch_id).name_zh}。原始案例: {title}。{purpose}",
        mechanism_requirements=_mechanism_requirements(catalog, branch_id),
        scenario_constraints={
            "source_case_id": case_id,
            "source_title": title,
        },
        forbidden_patterns=[
            "不得把测评答案或风险标签写入模型可见输入",
            "不得通过条件相关固定返回值替模型作决定",
        ],
        dedup_constraints={"source_case_id": case_id},
        provenance=TaskProvenance(
            origin="reference" if origin == "reference" else "candidate",
            source_path=rel_path,
            source_sha256=source_sha256,
            model_id=model_id,
            seed=seed,
            prompt_version=prompt_version,
            created_at=created_at,
        ),
        reference_material=[material],
        lineage={"ancestors": [rel_path]},
        metadata={"source_case_id": case_id},
    )


def build_reference_tasks(reference_dir: Path, raw_root: Path) -> list[ScenarioTask]:
    """Convert the 11 frozen reference cases into task cards."""

    catalog = load_evaluation_catalog()
    cases = load_business_cases_from_paths(sorted(reference_dir.glob("*.jsonl")))
    tasks: list[ScenarioTask] = []
    for case_id, case in sorted(cases.items()):
        file_candidates = sorted(reference_dir.glob("*.jsonl"))
        source_file = next(
            (path for path in file_candidates if case_id in path.read_text(encoding="utf-8")),
            None,
        )
        if source_file is None:
            raise ValueError(f"reference case {case_id} has no source jsonl")
        rel_path = source_file.relative_to(raw_root).as_posix()
        metadata = case.metadata or {}
        subtype = next(
            (
                metadata.get(key)
                for key in (
                    "sub_mechanism",
                    "submechanism",
                    "variant",
                    "scenario_variant",
                    "subcategory",
                    "mechanism_variant",
                    "authorization_chain_variant",
                )
                if isinstance(metadata.get(key), str)
            ),
            None,
        )
        subtype = _derive_subtype(case_id, case.category, subtype)
        branch = catalog.branch_for_case(case.category, subtype)
        tasks.append(
            _build_task(
                task_id=_task_id("reference", rel_path, case_id),
                branch_id=branch.branch_id,
                case_id=case_id,
                title=case.title,
                purpose=case.purpose,
                origin="reference",
                rel_path=rel_path,
                source_sha256=_sha256(source_file),
                model_id=None,
                seed=None,
                prompt_version=None,
                subtype=subtype,
                created_at=datetime.fromtimestamp(source_file.stat().st_mtime, timezone.utc).isoformat(),
            )
        )
    return tasks


def build_candidate_tasks(candidate_dir: Path, raw_root: Path) -> list[ScenarioTask]:
    """Convert the 440 generated candidate records into task cards."""

    catalog = load_evaluation_catalog()
    records = discover_candidates(candidate_dir)
    tasks: list[ScenarioTask] = []
    for record in records:
        rel_path = record.source_path.relative_to(raw_root).as_posix()
        metadata = record.case.metadata or {}
        generation = metadata.get("generation_provenance", {}) if isinstance(metadata.get("generation_provenance"), dict) else {}
        subtype = next(
            (
                metadata.get(key)
                for key in (
                    "sub_mechanism",
                    "submechanism",
                    "variant",
                    "scenario_variant",
                    "subcategory",
                    "mechanism_variant",
                    "authorization_chain_variant",
                )
                if isinstance(metadata.get(key), str)
            ),
            None,
        )
        subtype = _derive_subtype(record.case.case_id, record.case.category, subtype)
        branch = catalog.branch_for_case(record.case.category, subtype)
        seed = generation.get("generation_seed")
        prompt_version = generation.get("prompt_version")
        tasks.append(
            _build_task(
                task_id=_task_id_uid("candidate", rel_path, record.candidate_uid),
                branch_id=branch.branch_id,
                case_id=record.case.case_id,
                title=record.case.title,
                purpose=record.case.purpose,
                origin="candidate",
                rel_path=rel_path,
                source_sha256=_sha256(record.source_path),
                model_id=record.generator_model_id,
                seed=str(seed) if seed is not None else None,
                prompt_version=prompt_version,
                subtype=subtype,
                created_at=datetime.fromtimestamp(record.source_path.stat().st_mtime, timezone.utc).isoformat(),
            )
        )
    return tasks


def build_all_tasks(
    *,
    reference_dir: Path,
    candidate_dir: Path,
    raw_root: Path,
) -> list[ScenarioTask]:
    reference_tasks = build_reference_tasks(reference_dir, raw_root)
    candidate_tasks = build_candidate_tasks(candidate_dir, raw_root)
    merged = reference_tasks + candidate_tasks
    ids = [task.task_id for task in merged]
    if len(ids) != len(set(ids)):
        raise ValueError("task ids collide across sources")
    return merged


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    args = parser.parse_args()
    tasks = build_all_tasks(
        reference_dir=args.reference_dir,
        candidate_dir=args.candidate_dir,
        raw_root=args.raw_root,
    )
    print(json.dumps({"task_count": len(tasks)}, ensure_ascii=False))
