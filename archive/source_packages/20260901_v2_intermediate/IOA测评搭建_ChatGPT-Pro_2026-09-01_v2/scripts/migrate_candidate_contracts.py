#!/usr/bin/env python
"""Migrate old candidates into the unified generic-scoring dataset.

Original candidate evidence is never modified.  Only candidates for which a
contract can be derived from explicit tool/state evidence and which pass the
strict generated-case gates are written to ``migrated_cases.jsonl``.  Others
are recorded in ``quarantine.jsonl`` with an actionable reason.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.business_protocol.contract_migration import (  # noqa: E402
    ContractMigrationError,
    derive_generic_contract,
)
from src.evaluation.business_protocol.models import BusinessCaseSpec  # noqa: E402
from src.evaluation.business_protocol.validation import validate_generated_case  # noqa: E402
from src.evaluation.candidate_review import audit_candidates, discover_candidates  # noqa: E402
from src.evaluation.candidate_review.deterministic import CandidateRecord  # noqa: E402


DEFAULT_SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"
DEFAULT_OUTPUT = ROOT / "data" / "candidate_migrations" / "批量生成-第01轮"


def _new_case_id(record: CandidateRecord) -> str:
    digest = hashlib.sha256(record.candidate_uid.encode("utf-8")).hexdigest()[:16]
    return f"migrated-{digest}"


def _review_one(record: CandidateRecord):
    reviews, _ = audit_candidates([record])
    return reviews[0]


def _prepare_candidate(record: CandidateRecord) -> tuple[CandidateRecord, list[str]]:
    """Apply only repairs whose source is unambiguous and externally recorded."""
    case = record.case
    notes: list[str] = []
    if case.category == "TRA":
        current = str(case.metadata.get("sub_mechanism", ""))
        match = re.search(r"__(drift|vague)__", record.batch_id)
        inferred = match.group(1) if match else ""
        if current not in {"drift", "vague"} and inferred:
            metadata = dict(case.metadata)
            metadata["sub_mechanism"] = inferred
            case = case.model_copy(update={"metadata": metadata})
            notes.append(f"sub_mechanism_from_batch:{inferred}")
    return CandidateRecord(
        case=case,
        source_path=record.source_path,
        generator_model_id=record.generator_model_id,
        item_name=record.item_name,
        batch_id=record.batch_id,
    ), notes


def migrate(source: Path, output: Path) -> dict[str, object]:
    records = discover_candidates(source)
    if not records:
        raise ValueError(f"没有找到候选数据：{source}")
    output.mkdir(parents=True, exist_ok=True)
    migrated_path = output / "migrated_cases.jsonl"
    quarantine_path = output / "quarantine.jsonl"
    migrated_lines: list[str] = []
    quarantine: list[dict[str, object]] = []
    reason_counts: dict[str, int] = {}

    for record in records:
        prepared, repair_notes = _prepare_candidate(record)
        review = _review_one(prepared)
        if not review.passed:
            reasons = [f"{item.code}: {item.message}" for item in review.findings if item.severity == "ERROR"]
            for reason in reasons:
                reason_counts[reason.split(":", 1)[0]] = reason_counts.get(reason.split(":", 1)[0], 0) + 1
            quarantine.append({
                "candidate_uid": record.candidate_uid,
                "source_path": str(record.source_path),
                "case_id": record.case.case_id,
                "status": "QUARANTINED",
                "reason_codes": [item.code for item in review.findings if item.severity == "ERROR"],
                "reasons": reasons,
                "repair_notes": repair_notes,
            })
            continue
        try:
            contract = derive_generic_contract(prepared.case)
            payload = prepared.case.model_dump(mode="json")
            original_id = payload["case_id"]
            payload["case_id"] = _new_case_id(record)
            metadata = dict(payload.get("metadata") or {})
            metadata["migration"] = {
                "status": "CONTRACT_DRAFT",
                "source_case_id": original_id,
                "source_candidate_uid": record.candidate_uid,
                "source_batch_id": record.batch_id,
                "source_generator_model_id": record.generator_model_id,
                "contract_derivation": "explicit_tools_and_state_writes_v1",
                "requires_semantic_review": True,
                "deterministic_repairs": repair_notes,
            }
            payload["metadata"] = metadata
            payload["scoring_contract"] = contract.model_dump(mode="json")
            migrated_case = BusinessCaseSpec.model_validate(payload)
            validate_generated_case(migrated_case)
            migrated_lines.append(json.dumps({
                "schema_version": "business_case_spec_v1",
                "case": migrated_case.model_dump(mode="json"),
            }, ensure_ascii=False, separators=(",", ":")))
        except (ContractMigrationError, ValueError) as exc:
            code = "CONTRACT_DERIVATION_FAILED" if isinstance(exc, ContractMigrationError) else "STRICT_ADMISSION_FAILED"
            reason_counts[code] = reason_counts.get(code, 0) + 1
            quarantine.append({
                "candidate_uid": record.candidate_uid,
                "source_path": str(record.source_path),
                "case_id": record.case.case_id,
                "status": "QUARANTINED",
                "reason_codes": [code],
                "reasons": [str(exc)],
                "repair_notes": repair_notes,
            })

    migrated_path.write_text("\n".join(migrated_lines) + ("\n" if migrated_lines else ""), encoding="utf-8")
    quarantine_path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in quarantine)
        + ("\n" if quarantine else ""),
        encoding="utf-8",
    )
    summary = {
        "schema_version": "candidate_contract_migration_summary_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source),
        "output": str(output),
        "candidate_count": len(records),
        "migrated_count": len(migrated_lines),
        "quarantined_count": len(quarantine),
        "reason_counts": dict(sorted(reason_counts.items())),
        "artifacts": {
            "migrated_cases": str(migrated_path),
            "quarantine": str(quarantine_path),
        },
        "review_status": "CONTRACT_DRAFTS_REQUIRE_SEMANTIC_AND_HUMAN_REVIEW",
    }
    (output / "migration_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output / "README.md").write_text(
        "# 候选契约迁移\n\n"
        "`migrated_cases.jsonl` 只包含通过严格结构和准入校验的契约草案；\n"
        "`quarantine.jsonl` 保留无法从现有证据可靠推导规则的候选及原因。\n"
        "契约草案仍需语义互审和人工审核，不能直接视为正式数据集。\n",
        encoding="utf-8",
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        summary = migrate(args.source, args.output)
    except Exception as exc:
        print(json.dumps({"status": "FAILED", "error": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "COMPLETED", **summary}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
