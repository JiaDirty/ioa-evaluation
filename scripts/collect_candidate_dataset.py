#!/usr/bin/env python
"""Collect strict, generically scored candidates from a generated batch.

The source batch is read-only.  Accepted cases are copied into one JSONL file;
all other cases stay traceable in a rejection index with deterministic findings.
Warnings are retained in the report for later semantic and human review.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.business_protocol.dataset import (  # noqa: E402
    load_evaluation_dataset,
)
from src.evaluation.candidate_review import audit_candidates, discover_candidates  # noqa: E402
from src.evaluation.scenario_generation import (  # noqa: E402
    AuthoringScenarioResponse,
    BlueprintScenarioResponse,
    compile_authoring_response,
    compile_blueprint_response,
)


DEFAULT_SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第02轮-通用契约"
DEFAULT_OUTPUT = ROOT / "data" / "candidate_datasets" / "第02轮-严格候选"


def collect(source: Path, output: Path) -> dict[str, object]:
    records = discover_candidates(source)
    if not records:
        raise ValueError(f"没有找到已展开候选：{source}")
    reviews, duplicates = audit_candidates(records)
    by_uid = {review.candidate_uid: review for review in reviews}
    exact_duplicate_uids = {
        uid
        for pair in duplicates
        if pair["kind"] == "EXACT_CONTENT"
        for uid in (pair["candidate_uid_a"], pair["candidate_uid_b"])
    }

    output.mkdir(parents=True, exist_ok=True)
    accepted: list[str] = []
    index: list[dict[str, object]] = []
    rejected: list[dict[str, object]] = []
    for record in records:
        review = by_uid[record.candidate_uid]
        warning_codes = [
            item.code for item in review.findings if item.severity == "WARNING"
        ]
        common = {
            "candidate_uid": record.candidate_uid,
            "case_id": record.case.case_id,
            "category": record.case.category,
            "generator_model_id": record.generator_model_id,
            "batch_id": record.batch_id,
            "source_path": str(record.source_path),
            "warning_codes": warning_codes,
        }
        authoring_error = _authoring_recompile_error(record)
        if authoring_error is not None:
            rejected.append({
                **common,
                "status": "REJECTED",
                "reason_codes": ["AUTHORING_RECOMPILE_FAILED"],
                "reasons": [authoring_error],
            })
            continue
        if not review.passed:
            rejected.append({
                **common,
                "status": "REJECTED",
                "reason_codes": [
                    item.code for item in review.findings if item.severity == "ERROR"
                ],
                "reasons": [
                    item.message for item in review.findings if item.severity == "ERROR"
                ],
            })
            continue
        if record.candidate_uid in exact_duplicate_uids:
            rejected.append({
                **common,
                "status": "REJECTED_DUPLICATE",
                "reason_codes": ["EXACT_CONTENT_DUPLICATE"],
                "reasons": ["与同批次另一候选正文完全重复。"],
            })
            continue
        accepted.append(json.dumps({
            "schema_version": "business_case_spec_v1",
            "case": record.case.model_dump(mode="json"),
        }, ensure_ascii=False, separators=(",", ":")))
        index.append({**common, "status": "ACCEPTED_FOR_SEMANTIC_REVIEW"})

    accepted_path = output / "accepted_cases.jsonl"
    index_path = output / "accepted_index.jsonl"
    rejected_path = output / "rejected_index.jsonl"
    accepted_path.write_text("\n".join(accepted) + ("\n" if accepted else ""), encoding="utf-8")
    index_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in index)
        + ("\n" if index else ""),
        encoding="utf-8",
    )
    rejected_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in rejected)
        + ("\n" if rejected else ""),
        encoding="utf-8",
    )

    if accepted:
        dataset = load_evaluation_dataset([accepted_path], profile="generic_expandable")
        contract_versions = dataset.report.contract_versions
    else:
        contract_versions = {}
    accepted_counts = Counter(
        (item["generator_model_id"], item["category"]) for item in index
    )
    summary = {
        "schema_version": "candidate_dataset_collection_summary_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source),
        "output": str(output),
        "discovered_count": len(records),
        "accepted_count": len(index),
        "rejected_count": len(rejected),
        "duplicate_pair_count": len(duplicates),
        "contract_versions": contract_versions,
        "accepted_by_model_and_category": [
            {"model": model, "category": category, "count": count}
            for (model, category), count in sorted(accepted_counts.items())
        ],
        "artifacts": {
            "accepted_cases": str(accepted_path),
            "accepted_index": str(index_path),
            "rejected_index": str(rejected_path),
        },
        "review_status": "ACCEPTED_CASES_REQUIRE_SEMANTIC_AND_HUMAN_REVIEW",
    }
    (output / "collection_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# 严格候选集\n\n"
        "`accepted_cases.jsonl` 已通过结构和确定性检查，但仍需语义互审及人工/外包审核。\n"
        "`rejected_index.jsonl` 只记录问题和原始路径，不删除原始候选。\n",
        encoding="utf-8",
    )
    return summary


def _authoring_recompile_error(record: object) -> str | None:
    source_path = Path(record.source_path)
    response_path = source_path.parent / "candidate_batch.json"
    if not response_path.exists():
        return None
    try:
        raw = json.loads(response_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return f"作者响应无法读取：{exc}"
    version = raw.get("prompt_version")
    if version not in {
        "ioa_scenario_generation_v7_authoring",
        "ioa_scenario_generation_v8_blueprint",
        "ioa_scenario_generation_v9_blueprint_sequences",
    }:
        return None
    try:
        response = (
            AuthoringScenarioResponse.model_validate(raw)
            if version == "ioa_scenario_generation_v7_authoring"
            else BlueprintScenarioResponse.model_validate(raw)
        )
        provenance = record.case.metadata.get("generation_provenance")
        compiler = (
            compile_authoring_response
            if version == "ioa_scenario_generation_v7_authoring"
            else compile_blueprint_response
        )
        rebuilt = compiler(
            response,
            case_id=record.case.case_id,
            category=record.case.category,
            provenance=provenance if isinstance(provenance, dict) else None,
        )
        if rebuilt.model_dump(mode="json") != record.case.model_dump(mode="json"):
            return "作者响应重新编译后与已展开场景不一致。"
    except Exception as exc:
        return f"作者响应未通过当前编译和质量门槛：{exc}"
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        summary = collect(args.source, args.output)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "COMPLETED", **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
