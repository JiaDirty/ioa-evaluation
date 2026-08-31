#!/usr/bin/env python
"""Merge contract-migrated candidate files into one expandable dataset.

Migration outputs are treated as immutable evidence.  This command only reads
their ``migrated_cases.jsonl`` files, validates the combined dataset with the
generic profile, and writes a new JSONL dataset plus a machine-readable
manifest.  Quarantined records never enter the merged dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.business_protocol.dataset import load_evaluation_dataset  # noqa: E402


DEFAULT_SOURCES = (
    ROOT / "data" / "candidate_migrations" / "第10轮-蓝图v9.2全量迁移",
    ROOT / "data" / "candidate_migrations" / "第12轮-授权链迁移",
)
DEFAULT_OUTPUT = ROOT / "data" / "candidate_datasets" / "统一通用候选-第01轮"


def _resolve_migrated_files(sources: Iterable[Path]) -> tuple[Path, ...]:
    files: list[Path] = []
    for source in sources:
        source = source.expanduser()
        if source.is_dir():
            source = source / "migrated_cases.jsonl"
        if not source.is_file():
            raise ValueError(f"迁移结果不存在：{source}")
        if source.name != "migrated_cases.jsonl":
            raise ValueError(f"迁移结果文件必须命名为 migrated_cases.jsonl：{source}")
        files.append(source.resolve())
    if not files:
        raise ValueError("至少需要一个迁移结果目录或 migrated_cases.jsonl 文件")
    return tuple(dict.fromkeys(files))


def _source_summary(source_file: Path) -> dict[str, object]:
    summary_path = source_file.parent / "migration_summary.json"
    if not summary_path.exists():
        return {"source": str(source_file), "summary": None}
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"无法读取迁移摘要 {summary_path}: {exc}") from exc
    return {
        "source": str(source_file),
        "candidate_count": summary.get("candidate_count"),
        "migrated_count": summary.get("migrated_count"),
        "quarantined_count": summary.get("quarantined_count"),
        "review_status": summary.get("review_status"),
    }


def merge(sources: Iterable[Path], output: Path) -> dict[str, object]:
    files = _resolve_migrated_files(sources)
    dataset = load_evaluation_dataset(list(files), profile="generic_expandable")
    output.mkdir(parents=True, exist_ok=True)
    accepted_path = output / "accepted_cases.jsonl"
    lines = [
        json.dumps(
            {"schema_version": "business_case_spec_v1", "case": case.model_dump(mode="json")},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        for case in dataset.cases.values()
    ]
    accepted_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")

    by_model = Counter()
    for case in dataset.cases.values():
        provenance = case.metadata.get("migration", {})
        model = str(provenance.get("source_generator_model_id", "unknown"))
        by_model[model] += 1

    summary = {
        "schema_version": "unified_generic_dataset_summary_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "dataset_profile": dataset.report.profile,
        "case_count": dataset.report.case_count,
        "category_counts": dataset.report.category_counts,
        "contract_versions": dataset.report.contract_versions,
        "source_migrations": [_source_summary(path) for path in files],
        "cases_by_source_generator_model": dict(sorted(by_model.items())),
        "review_status": "STRUCTURE_VALID_CONTRACT_DRAFTS_REQUIRE_SEMANTIC_AND_HUMAN_REVIEW",
        "artifacts": {"accepted_cases": str(accepted_path.resolve())},
    }
    (output / "dataset_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# 统一通用候选集\n\n"
        "`accepted_cases.jsonl` 合并了已从原始候选明确推导出 `generic_scoring_v1` "
        "契约、并通过结构准入的记录。\n"
        "本目录不是最终正式数据集；每条记录仍需语义互审和人工/外包审核。\n"
        "原始候选、迁移摘要和隔离记录均保留在各自源目录中。\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        action="append",
        type=Path,
        dest="sources",
        help="迁移结果目录或 migrated_cases.jsonl；可重复指定。",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    sources = args.sources or list(DEFAULT_SOURCES)
    try:
        summary = merge(sources, args.output)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "COMPLETED", **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
