#!/usr/bin/env python
"""Review a stable two-of-five sample with an eight-model rotation."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.candidate_review import (  # noqa: E402
    SemanticReview,
    audit_candidates,
    discover_candidates,
)
from src.evaluation.candidate_review.deterministic import CandidateRecord  # noqa: E402
from src.evaluation.candidate_review.prompt import (  # noqa: E402
    SYSTEM_MESSAGE,
    build_review_message,
)
from src.evaluation.catalog import load_evaluation_catalog  # noqa: E402
from src.llm.client import OpenAIClient  # noqa: E402
from src.llm.config import AgentLLMConfig, load_agent_llm_config  # noqa: E402


PROFILE_PATH = ROOT / "config" / "rotation_review_model_profiles.yaml"
MODEL_RING = (
    "gpt-5.6-luna",
    "deepseek-v4-flash",
    "gemini-3.7-flash",
    "gpt-5.6-sol",
    "claude-opus-5",
    "glm-5.3-flash",
    "deepseek-v4-pro-0813",
    "qwen3.8-flash",
)
# Each pair is (reviewer, generator): the left model reviews the right model.
REVIEWER_TO_GENERATOR = tuple(
    (MODEL_RING[index], MODEL_RING[(index + 1) % len(MODEL_RING)])
    for index in range(len(MODEL_RING))
)
GENERATOR_TO_REVIEWER = {
    generator: reviewer for reviewer, generator in REVIEWER_TO_GENERATOR
}
EXPECTED_ITEMS = (
    "跨系统级联扩散__default",
    "生态错误共识__default",
    "谣言扩散__default",
    "规范漂移__default",
    "激励错配__default",
    "判断让渡__default",
    "讨论扭曲__default",
    "能动性弱化__default",
    "信任与授权链__drift",
    "信任与授权链__vague",
    "资格申报__default",
)
EXPECTED_PER_GROUP = 5
DEFAULT_SAMPLE_PER_GROUP = 2


@dataclass(frozen=True)
class RotationJob:
    index: int
    record: CandidateRecord
    reviewer_model_id: str
    deterministic_passed: bool
    category_name_zh: str
    duplicate_ids: tuple[str, ...]


def stable_rank(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def load_profiles(path: Path = PROFILE_PATH) -> dict[str, dict[str, Any]]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    default = payload.get("default") or {}
    profiles = {
        model_id: {**default, **profile}
        for model_id, profile in (payload.get("models") or {}).items()
        if profile.get("enabled", True)
    }
    if set(profiles) != set(MODEL_RING):
        missing = sorted(set(MODEL_RING) - set(profiles))
        extra = sorted(set(profiles) - set(MODEL_RING))
        raise ValueError(f"八模型审核配置不完整：missing={missing}, extra={extra}")
    return profiles


def validate_candidate_matrix(records: list[CandidateRecord]) -> None:
    expected_total = len(MODEL_RING) * len(EXPECTED_ITEMS) * EXPECTED_PER_GROUP
    if len(records) != expected_total:
        raise ValueError(f"候选总数应为 {expected_total}，实际为 {len(records)}")
    candidate_uids = [record.candidate_uid for record in records]
    if len(candidate_uids) != len(set(candidate_uids)):
        raise ValueError("候选 candidate_uid 不唯一")
    actual_models = {record.generator_model_id for record in records}
    if actual_models != set(MODEL_RING):
        raise ValueError(
            "生成模型集合不匹配："
            f"missing={sorted(set(MODEL_RING) - actual_models)}, "
            f"extra={sorted(actual_models - set(MODEL_RING))}"
        )
    actual_items = {record.item_name for record in records}
    if actual_items != set(EXPECTED_ITEMS):
        raise ValueError(
            "生成项集合不匹配："
            f"missing={sorted(set(EXPECTED_ITEMS) - actual_items)}, "
            f"extra={sorted(actual_items - set(EXPECTED_ITEMS))}"
        )
    counts = Counter((record.item_name, record.generator_model_id) for record in records)
    invalid = {
        f"{item}::{model}": counts[(item, model)]
        for item in EXPECTED_ITEMS
        for model in MODEL_RING
        if counts[(item, model)] != EXPECTED_PER_GROUP
    }
    if invalid:
        raise ValueError(f"每个生成项和模型必须恰有 5 条候选：{invalid}")


def select_rotation_records(
    records: list[CandidateRecord],
    *,
    per_group: int = DEFAULT_SAMPLE_PER_GROUP,
) -> list[tuple[CandidateRecord, str]]:
    validate_candidate_matrix(records)
    if not 1 <= per_group <= EXPECTED_PER_GROUP:
        raise ValueError(f"per_group 必须在 1 到 {EXPECTED_PER_GROUP} 之间")
    grouped: dict[tuple[str, str], list[CandidateRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.item_name, record.generator_model_id)].append(record)
    selected: list[tuple[CandidateRecord, str]] = []
    for item_name in EXPECTED_ITEMS:
        for generator_model_id in MODEL_RING:
            ranked = sorted(
                grouped[(item_name, generator_model_id)],
                key=lambda record: stable_rank(
                    item_name, generator_model_id, record.candidate_uid
                ),
            )
            reviewer = GENERATOR_TO_REVIEWER[generator_model_id]
            if reviewer == generator_model_id:
                raise ValueError(f"轮转配置产生自审：{generator_model_id}")
            selected.extend((record, reviewer) for record in ranked[:per_group])
    return selected


def _duplicate_map(duplicates: list[dict[str, Any]]) -> dict[str, list[str]]:
    mapped: dict[str, list[str]] = defaultdict(list)
    for pair in duplicates:
        if pair["kind"] == "DUPLICATE_CASE_ID":
            for uid in pair["candidate_uids"]:
                mapped[uid].extend(
                    other for other in pair["candidate_uids"] if other != uid
                )
        else:
            mapped[pair["candidate_uid_a"]].append(pair["candidate_uid_b"])
            mapped[pair["candidate_uid_b"]].append(pair["candidate_uid_a"])
    return {uid: sorted(set(values)) for uid, values in mapped.items()}


def _sum_usage(total: dict[str, int], current: dict[str, int] | None) -> None:
    for key, value in (current or {}).items():
        total[key] = total.get(key, 0) + int(value or 0)


def review_one(job: RotationJob, profile: dict[str, Any], output_dir: Path) -> dict:
    record = job.record
    reviewer = job.reviewer_model_id
    uid_hash = hashlib.sha256(record.candidate_uid.encode("utf-8")).hexdigest()[:16]
    case_dir = output_dir / "model_reviews" / uid_hash / reviewer
    case_dir.mkdir(parents=True, exist_ok=True)
    result_path = case_dir / "review.json"
    if result_path.exists():
        try:
            review = SemanticReview.model_validate_json(
                result_path.read_text(encoding="utf-8")
            )
            if review.case_id != record.case.case_id:
                raise ValueError("已有审核结果 case_id 与候选不一致")
            if review.candidate_uid != record.candidate_uid:
                raise ValueError("已有审核结果 candidate_uid 与候选不一致")
            if review.reviewer_model_id != reviewer:
                raise ValueError("已有审核结果 reviewer_model_id 与轮转配置不一致")
            return {"status": "REUSED", "review": review, "path": result_path}
        except Exception:
            pass

    base = load_agent_llm_config()
    config = AgentLLMConfig(
        provider=base.provider,
        model=reviewer,
        api_key=base.api_key,
        base_url=base.base_url,
        temperature=float(profile["temperature"]),
        top_p=float(profile["top_p"]),
        max_completion_tokens=int(profile["max_completion_tokens"]),
        context_window_tokens=base.context_window_tokens,
        model_max_completion_tokens=int(profile["max_completion_tokens"]),
        retry_count=1,
        retry_delay=2.0,
        timeout=int(profile["timeout"]),
    )
    client = OpenAIClient(config)
    user_message = build_review_message(
        record.case,
        category_name_zh=job.category_name_zh,
        reviewer_model_id=reviewer,
        candidate_uid=record.candidate_uid,
        known_duplicate_case_ids=list(job.duplicate_ids),
    )
    attempts: list[dict[str, Any]] = []
    total_usage: dict[str, int] = {}
    final_review: SemanticReview | None = None
    final_error: Exception | None = None
    started = time.perf_counter()

    for logical_attempt in (1, 2):
        attempt: dict[str, Any] = {"attempt": logical_attempt}
        try:
            raw = client.generate_with_system(
                SYSTEM_MESSAGE,
                user_message,
                response_format={"type": "json_object"},
                temperature=float(profile["temperature"]),
                top_p=float(profile["top_p"]),
                max_completion_tokens=int(profile["max_completion_tokens"]),
                reasoning_effort=profile.get("reasoning_effort"),
                retry_count=1,
            )
            review = SemanticReview.model_validate(json.loads(raw))
            if review.case_id != record.case.case_id:
                raise ValueError("审核结果 case_id 与输入不一致")
            if review.candidate_uid != record.candidate_uid:
                raise ValueError("审核结果 candidate_uid 与输入不一致")
            if review.reviewer_model_id != reviewer:
                raise ValueError("审核结果 reviewer_model_id 与实际审核模型不一致")
            final_review = review
            attempt["status"] = "COMPLETED"
            final_error = None
        except Exception as exc:
            final_error = exc
            attempt["status"] = "FAILED"
            attempt["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            _sum_usage(total_usage, client.last_usage)
            attempt.update({
                "request": client.last_request_payload,
                "response": client.last_response_payload,
                "provider_calls": client.last_provider_calls,
                "latency_ms": client.last_latency_ms,
                "request_budget": client.last_request_budget,
            })
            attempts.append(attempt)
        if final_review is not None:
            break
        if logical_attempt == 1:
            time.sleep(2.0)

    (case_dir / "attempts.json").write_text(
        json.dumps(attempts, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    last_attempt = attempts[-1]
    (case_dir / "request.json").write_text(
        json.dumps(last_attempt.get("request"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (case_dir / "response.json").write_text(
        json.dumps(last_attempt.get("response"), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    if final_review is None:
        error_payload = {
            "status": "FAILED",
            "candidate_uid": record.candidate_uid,
            "case_id": record.case.case_id,
            "generator_model_id": record.generator_model_id,
            "reviewer_model_id": reviewer,
            "attempt_count": len(attempts),
            "error": str(final_error),
        }
        error_path = case_dir / "error.json"
        error_path.write_text(
            json.dumps(error_payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {**error_payload, "path": error_path}

    result_path.write_text(final_review.model_dump_json(indent=2), encoding="utf-8")
    metadata = {
        "status": "COMPLETED",
        "candidate_uid": record.candidate_uid,
        "case_id": record.case.case_id,
        "generator_model_id": record.generator_model_id,
        "reviewer_model_id": reviewer,
        "decision": final_review.decision,
        "attempt_count": len(attempts),
        "retry_count": len(attempts) - 1,
        "usage": total_usage or None,
        "latency_ms": elapsed_ms,
        "response_metadata": client.last_response_metadata,
    }
    (case_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"status": "COMPLETED", "review": final_review, "path": result_path}


def _write_deterministic_outputs(
    output_dir: Path,
    candidate_root: Path,
    reviews: list,
    duplicates: list[dict[str, Any]],
) -> dict[str, Any]:
    review_path = output_dir / "deterministic_reviews.jsonl"
    review_path.write_text(
        "\n".join(item.model_dump_json() for item in reviews) + "\n",
        encoding="utf-8",
    )
    duplicate_path = output_dir / "duplicate_pairs.json"
    duplicate_path.write_text(
        json.dumps(duplicates, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    finding_counts = Counter(
        (finding.severity, finding.code)
        for review in reviews
        for finding in review.findings
    )
    summary = {
        "schema_version": "candidate_deterministic_summary_v1",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate_root": str(candidate_root),
        "candidate_count": len(reviews),
        "passed_count": sum(item.passed for item in reviews),
        "failed_count": sum(not item.passed for item in reviews),
        "duplicate_pair_count": len(duplicates),
        "finding_counts": [
            {"severity": severity, "code": code, "count": count}
            for (severity, code), count in sorted(finding_counts.items())
        ],
    }
    (output_dir / "deterministic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def _decision_stats(
    jobs: list[RotationJob], results: list[dict[str, Any]]
) -> dict[str, Any]:
    dimensions = {
        "by_generator_model": defaultdict(Counter),
        "by_reviewer_model": defaultdict(Counter),
        "by_evaluation_item": defaultdict(Counter),
    }
    status_counts: Counter[str] = Counter()
    decision_counts: Counter[str] = Counter()
    retry_count = 0
    rows: list[dict[str, Any]] = []
    for job, result in zip(jobs, results, strict=True):
        status = result["status"]
        review = result.get("review")
        decision = review.decision if review is not None else None
        status_counts[status] += 1
        if decision:
            decision_counts[decision] += 1
        for key, value in (
            ("by_generator_model", job.record.generator_model_id),
            ("by_reviewer_model", job.reviewer_model_id),
            ("by_evaluation_item", job.record.item_name),
        ):
            dimensions[key][value][decision or status] += 1
        metadata_path = Path(result["path"]).parent / "metadata.json"
        if metadata_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            retry_count += int(metadata.get("retry_count", 0))
        rows.append({
            "job_index": job.index,
            "status": status,
            "candidate_uid": job.record.candidate_uid,
            "case_id": job.record.case.case_id,
            "item_name": job.record.item_name,
            "generator_model_id": job.record.generator_model_id,
            "reviewer_model_id": job.reviewer_model_id,
            "decision": decision,
            "result_path": str(result["path"]),
            "error": result.get("error"),
        })
    return {
        "status_counts": dict(status_counts),
        "decision_counts": dict(decision_counts),
        "retry_count": retry_count,
        **{
            key: {name: dict(counts) for name, counts in sorted(values.items())}
            for key, values in dimensions.items()
        },
        "rows": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-root",
        type=Path,
        default=ROOT / "data" / "candidate_batches" / "批量生成-第01轮",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "data" / "candidate_reviews" / "八模型轮转互审-第01轮",
    )
    parser.add_argument("--per-group", type=int, default=DEFAULT_SAMPLE_PER_GROUP)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records = discover_candidates(args.candidate_root)
    reviews, duplicates = audit_candidates(records)
    selected = select_rotation_records(records, per_group=args.per_group)
    profiles = load_profiles()
    review_by_uid = {review.candidate_uid: review for review in reviews}
    duplicates_by_uid = _duplicate_map(duplicates)
    catalog = load_evaluation_catalog()
    category_names = {item.code: item.name_zh for item in catalog.categories}
    jobs = [
        RotationJob(
            index=index,
            record=record,
            reviewer_model_id=reviewer,
            deterministic_passed=review_by_uid[record.candidate_uid].passed,
            category_name_zh=category_names[record.case.category],
            duplicate_ids=tuple(duplicates_by_uid.get(record.candidate_uid, [])),
        )
        for index, (record, reviewer) in enumerate(selected, start=1)
    ]
    if any(job.record.generator_model_id == job.reviewer_model_id for job in jobs):
        raise ValueError("审核任务中存在自审")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    deterministic_summary = _write_deterministic_outputs(
        args.output_dir, args.candidate_root, reviews, duplicates
    )
    manifest = {
        "schema_version": "candidate_rotation_review_manifest_v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate_root": str(args.candidate_root),
        "candidate_count": len(records),
        "item_count": len(EXPECTED_ITEMS),
        "model_count": len(MODEL_RING),
        "candidates_per_group": EXPECTED_PER_GROUP,
        "selected_per_group": args.per_group,
        "review_job_count": len(jobs),
        "self_review_job_count": 0,
        "selection_method": "sha256(item_name|generator_model_id|candidate_uid)",
        "rotation_semantics": "reviewer_model_id reviews generator_model_id",
        "rotation": [
            {"reviewer_model_id": reviewer, "generator_model_id": generator}
            for reviewer, generator in REVIEWER_TO_GENERATOR
        ],
        "jobs": [
            {
                "job_index": job.index,
                "candidate_uid": job.record.candidate_uid,
                "case_id": job.record.case.case_id,
                "item_name": job.record.item_name,
                "category_name_zh": job.category_name_zh,
                "generator_model_id": job.record.generator_model_id,
                "reviewer_model_id": job.reviewer_model_id,
                "source_path": str(job.record.source_path),
                "deterministic_passed": job.deterministic_passed,
            }
            for job in jobs
        ],
    }
    manifest_path = args.output_dir / "rotation_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    preview = {
        key: manifest[key]
        for key in (
            "candidate_count",
            "item_count",
            "model_count",
            "candidates_per_group",
            "selected_per_group",
            "review_job_count",
            "self_review_job_count",
        )
    }
    preview["deterministic_passed_count"] = deterministic_summary["passed_count"]
    preview["deterministic_failed_count"] = deterministic_summary["failed_count"]
    print(json.dumps(preview, ensure_ascii=False, indent=2), flush=True)
    if args.dry_run:
        return 0

    results_by_index: dict[int, dict[str, Any]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(review_one, job, profiles[job.reviewer_model_id], args.output_dir): job
            for job in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            job = futures[future]
            result = future.result()
            results_by_index[job.index] = result
            review = result.get("review")
            print(json.dumps({
                "job_index": job.index,
                "status": result["status"],
                "case_id": job.record.case.case_id,
                "generator_model_id": job.record.generator_model_id,
                "reviewer_model_id": job.reviewer_model_id,
                "decision": review.decision if review else None,
                "error": result.get("error"),
            }, ensure_ascii=False), flush=True)

    ordered_results = [results_by_index[job.index] for job in jobs]
    stats = _decision_stats(jobs, ordered_results)
    job_results_path = args.output_dir / "job_results.jsonl"
    job_results_path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in stats.pop("rows")) + "\n",
        encoding="utf-8",
    )
    summary = {
        "schema_version": "candidate_rotation_review_summary_v1",
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "candidate_count": len(records),
        "review_job_count": len(jobs),
        "completed_review_count": sum(
            result["status"] in {"COMPLETED", "REUSED"}
            for result in ordered_results
        ),
        "failed_review_count": sum(
            result["status"] == "FAILED" for result in ordered_results
        ),
        "self_review_job_count": 0,
        **stats,
        "manifest_path": str(manifest_path),
        "job_results_path": str(job_results_path),
    }
    (args.output_dir / "semantic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0 if summary["failed_review_count"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
