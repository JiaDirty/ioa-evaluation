#!/usr/bin/env python
"""Semantically review an already validated generic candidate dataset.

The input dataset is read-only.  Each selected candidate is reviewed by a
deterministically assigned pair of models that excludes the model that
generated it.  Requests, raw responses, validated review objects and a
consensus index are written to a separate output directory.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.business_protocol.dataset import load_evaluation_dataset  # noqa: E402
from src.evaluation.business_protocol.models import BusinessCaseSpec  # noqa: E402
from src.evaluation.candidate_review import (  # noqa: E402
    CandidateRecord,
    SemanticReview,
    audit_candidates,
)
from src.evaluation.catalog import load_evaluation_catalog  # noqa: E402
from scripts.review_candidate_sample import (  # noqa: E402
    load_profiles,
    review_one,
)


DEFAULT_SOURCE = ROOT / "data" / "candidate_datasets" / "统一通用候选-第01轮" / "accepted_cases.jsonl"
DEFAULT_OUTPUT = ROOT / "data" / "candidate_reviews" / "统一通用候选-第01轮语义审核"
TRUST_VARIANT_LABELS = {
    "drift": "转述授权扩大",
    "vague": "含糊授权越界",
}


def _stable_rank(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def logical_item_name(case: BusinessCaseSpec, category_names: dict[str, str]) -> str:
    category_name = category_names[case.category]
    if case.category == "TRA":
        variant = str(case.metadata.get("sub_mechanism", ""))
        label = TRUST_VARIANT_LABELS.get(variant, variant or "未标记子机制")
        return f"{category_name}：{label}"
    return category_name


def load_records(source: Path) -> list[CandidateRecord]:
    dataset = load_evaluation_dataset([source], profile="generic_expandable")
    category_names = {
        item.code: item.name_zh for item in load_evaluation_catalog().categories
    }
    records: list[CandidateRecord] = []
    for case in dataset.cases.values():
        migration = case.metadata.get("migration", {})
        provenance = case.metadata.get("generation_provenance", {})
        generator_model = str(
            migration.get("source_generator_model_id")
            or provenance.get("generator_model_id")
            or "unknown"
        )
        batch_id = str(
            migration.get("source_batch_id")
            or provenance.get("batch_id")
            or "merged-generic-dataset"
        )
        records.append(
            CandidateRecord(
                case=case,
                source_path=source.resolve(),
                generator_model_id=generator_model,
                item_name=logical_item_name(case, category_names),
                batch_id=batch_id,
            )
        )
    return records


def select_records(records: list[CandidateRecord], per_item: int) -> list[CandidateRecord]:
    if per_item <= 0:
        return sorted(records, key=lambda item: item.candidate_uid)
    grouped: dict[str, list[CandidateRecord]] = defaultdict(list)
    for record in records:
        grouped[record.item_name].append(record)
    selected: list[CandidateRecord] = []
    for item_name in sorted(grouped):
        candidates = sorted(
            grouped[item_name],
            key=lambda item: _stable_rank(
                item_name, item.case.case_id, item.generator_model_id
            ),
        )
        used_models: set[str] = set()
        for candidate in candidates:
            if candidate.generator_model_id in used_models:
                continue
            selected.append(candidate)
            used_models.add(candidate.generator_model_id)
            if len(used_models) == per_item:
                break
        if len(used_models) != per_item:
            raise ValueError(
                f"{item_name} 无法选出 {per_item} 个不同生成模型的候选"
            )
    return selected


def assign_reviewers(
    record: CandidateRecord,
    reviewer_ids: list[str],
    count: int,
) -> list[str]:
    eligible = [model for model in reviewer_ids if model != record.generator_model_id]
    eligible.sort(key=lambda model: _stable_rank(record.case.case_id, model))
    if len(eligible) < count:
        raise ValueError(
            f"{record.case.case_id} 没有足够的非生成模型审核员：{eligible}"
        )
    return eligible[:count]


def _duplicate_map(duplicates: list[dict[str, object]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for pair in duplicates:
        if pair["kind"] == "DUPLICATE_CASE_ID":
            ids = list(pair["candidate_uids"])
            for uid in ids:
                result[uid].extend(other for other in ids if other != uid)
        else:
            left = str(pair["candidate_uid_a"])
            right = str(pair["candidate_uid_b"])
            result[left].append(right)
            result[right].append(left)
    return {key: sorted(set(value)) for key, value in result.items()}


def _review_job(
    record: CandidateRecord,
    reviewer: str,
    profile: dict,
    output: Path,
    category_name: str,
    duplicate_ids: list[str],
) -> dict[str, object]:
    return review_one(
        record,
        reviewer,
        profile,
        output,
        category_name,
        duplicate_ids,
    )


def run_review(
    source: Path,
    output: Path,
    *,
    per_item: int = 0,
    reviewers_per_case: int = 2,
    workers: int = 8,
    dry_run: bool = False,
) -> dict[str, object]:
    records = load_records(source)
    if not records:
        raise ValueError(f"没有找到通用候选：{source}")
    deterministic, duplicates = audit_candidates(records)
    deterministic_by_uid = {item.candidate_uid: item for item in deterministic}
    selected = select_records(records, per_item)
    profiles = load_profiles()
    reviewer_ids = sorted(profiles)
    category_names = {
        item.code: item.name_zh for item in load_evaluation_catalog().categories
    }
    duplicate_map = _duplicate_map(duplicates)

    manifest_cases: list[dict[str, object]] = []
    jobs: list[tuple[CandidateRecord, str]] = []
    for record in selected:
        reviewers = assign_reviewers(record, reviewer_ids, reviewers_per_case)
        manifest_cases.append({
            "candidate_uid": record.candidate_uid,
            "case_id": record.case.case_id,
            "item_name": record.item_name,
            "category_name_zh": category_names[record.case.category],
            "generator_model_id": record.generator_model_id,
            "source_path": str(record.source_path),
            "deterministic_passed": deterministic_by_uid[record.candidate_uid].passed,
            "reviewer_model_ids": reviewers,
        })
        jobs.extend((record, reviewer) for reviewer in reviewers)

    output.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "generic_dataset_semantic_review_manifest_v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source.resolve()),
        "selection": {
            "per_item": per_item,
            "selected_case_count": len(selected),
            "logical_item_count": len({record.item_name for record in selected}),
            "logical_item_counts": dict(
                sorted(Counter(record.item_name for record in selected).items())
            ),
        },
        "reviewers_per_case": reviewers_per_case,
        "review_job_count": len(jobs),
        "cases": manifest_cases,
    }
    (output / "review_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if dry_run:
        return {
            "status": "DRY_RUN",
            "candidate_count": len(records),
            "selected_case_count": len(selected),
            "logical_item_count": len({record.item_name for record in selected}),
            "logical_item_counts": manifest["selection"]["logical_item_counts"],
            "review_job_count": len(jobs),
            "self_review_count": sum(
                record.generator_model_id == reviewer for record, reviewer in jobs
            ),
            "deterministic_failed_count": sum(
                not deterministic_by_uid[record.candidate_uid].passed
                for record in selected
            ),
        }

    results: list[dict[str, object]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _review_job,
                record,
                reviewer,
                profiles[reviewer],
                output,
                category_names[record.case.category],
                duplicate_map.get(record.candidate_uid, []),
            ): (record, reviewer)
            for record, reviewer in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            record, reviewer = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover - defensive worker guard
                result = {
                    "status": "FAILED",
                    "candidate_uid": record.candidate_uid,
                    "case_id": record.case.case_id,
                    "reviewer_model_id": reviewer,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            results.append(result)
            review = result.get("review")
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "case_id": record.case.case_id,
                        "reviewer_model_id": reviewer,
                        "decision": getattr(review, "decision", None),
                        "error": result.get("error"),
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    completed = [
        item for item in results if item["status"] in {"COMPLETED", "SKIPPED"}
    ]
    decisions = Counter(item["review"].decision for item in completed)
    reviews_by_uid: dict[str, list[SemanticReview]] = defaultdict(list)
    for item in completed:
        reviews_by_uid[item["review"].candidate_uid].append(item["review"])

    consensus_cases: list[dict[str, object]] = []
    consensus_counts: Counter[str] = Counter()
    per_item_consensus: dict[str, Counter[str]] = defaultdict(Counter)
    for manifest_case in manifest_cases:
        uid = str(manifest_case["candidate_uid"])
        case_reviews = reviews_by_uid.get(uid, [])
        case_decisions = [review.decision for review in case_reviews]
        if len(case_decisions) != reviewers_per_case:
            consensus = "INCOMPLETE"
        elif len(set(case_decisions)) > 1:
            consensus = "DISAGREEMENT"
        else:
            consensus = f"UNANIMOUS_{case_decisions[0]}"
        consensus_counts[consensus] += 1
        item_name = str(manifest_case["item_name"])
        per_item_consensus[item_name][consensus] += 1
        consensus_cases.append({
            **manifest_case,
            "consensus": consensus,
            "provisional_action": (
                "PROVISIONAL_ACCEPT"
                if consensus == "UNANIMOUS_ACCEPT"
                else "HUMAN_REVIEW"
            ),
            "reviews": [
                {
                    "reviewer_model_id": review.reviewer_model_id,
                    "decision": review.decision,
                    "confidence": review.confidence,
                    "critical_issues": review.critical_issues,
                    "revision_suggestions": review.revision_suggestions,
                }
                for review in case_reviews
            ],
        })

    consensus_path = output / "case_consensus.json"
    consensus_path.write_text(
        json.dumps(
            {
                "schema_version": "generic_dataset_case_consensus_v1",
                "cases": consensus_cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "status": "COMPLETED",
        "schema_version": "generic_dataset_semantic_review_summary_v1",
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": str(source.resolve()),
        "candidate_count": len(records),
        "selected_case_count": len(selected),
        "review_job_count": len(jobs),
        "completed_review_count": len(completed),
        "failed_review_count": len(results) - len(completed),
        "decision_counts": dict(decisions),
        "consensus_counts": dict(consensus_counts),
        "per_item_consensus": {
            item: dict(counts) for item, counts in sorted(per_item_consensus.items())
        },
        "duplicate_pair_count": len(duplicates),
        "case_consensus_path": str(consensus_path.resolve()),
    }
    (output / "semantic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--per-item",
        type=int,
        default=0,
        help="每个逻辑测评项最多审核多少个不同生成模型的候选；0 表示全部。",
    )
    parser.add_argument("--reviewers-per-case", type=int, default=2)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.per_item < 0 or args.reviewers_per_case < 1 or args.workers < 1:
        parser.error("--per-item、--reviewers-per-case 和 --workers 参数不合法")
    try:
        summary = run_review(
            args.source,
            args.output,
            per_item=args.per_item,
            reviewers_per_case=args.reviewers_per_case,
            workers=args.workers,
            dry_run=args.dry_run,
        )
    except Exception as exc:
        print(
            json.dumps(
                {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}"},
                ensure_ascii=False,
            )
        )
        return 2
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary.get("status") == "DRY_RUN":
        return 0
    return 0 if summary.get("failed_review_count", 1) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
