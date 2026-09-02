#!/usr/bin/env python
"""Cross-review a stratified candidate sample with independent AI Hub Mix models."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.evaluation.candidate_review import (  # noqa: E402
    SemanticReview,
    audit_candidates,
    discover_candidates,
)
from src.evaluation.candidate_review.prompt import (  # noqa: E402
    SYSTEM_MESSAGE,
    build_review_message,
)
from src.evaluation.catalog import load_evaluation_catalog  # noqa: E402
from src.llm.client import OpenAIClient  # noqa: E402
from src.llm.config import AgentLLMConfig, load_agent_llm_config  # noqa: E402

PROFILE_PATH = ROOT / "config" / "review_model_profiles.yaml"


def load_profiles() -> dict[str, dict]:
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8")) or {}
    default = payload.get("default") or {}
    return {
        model_id: {**default, **profile}
        for model_id, profile in (payload.get("models") or {}).items()
        if profile.get("enabled", True)
    }


def stable_rank(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def select_sample(records, per_item: int) -> list:
    grouped = defaultdict(list)
    for record in records:
        grouped[record.item_name].append(record)
    selected = []
    for item_name in sorted(grouped):
        candidates = sorted(
            grouped[item_name],
            key=lambda item: stable_rank(item_name, item.case.case_id, item.generator_model_id),
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
            raise ValueError(f"{item_name} 无法选出 {per_item} 个不同生成模型的候选")
    return selected


def assign_reviewers(record, reviewer_ids: list[str], count: int) -> list[str]:
    eligible = [model for model in reviewer_ids if model != record.generator_model_id]
    eligible.sort(key=lambda model: stable_rank(record.case.case_id, model))
    if len(eligible) < count:
        raise ValueError(f"{record.case.case_id} 没有足够的非生成模型审核员")
    return eligible[:count]


def review_one(record, reviewer_model: str, profile: dict, output_dir: Path,
               category_name_zh: str, duplicate_ids: list[str]) -> dict:
    uid_hash = hashlib.sha256(record.candidate_uid.encode("utf-8")).hexdigest()[:16]
    case_dir = output_dir / "model_reviews" / uid_hash / reviewer_model
    case_dir.mkdir(parents=True, exist_ok=True)
    result_path = case_dir / "review.json"
    if result_path.exists():
        try:
            review = SemanticReview.model_validate_json(result_path.read_text(encoding="utf-8"))
            return {"status": "SKIPPED", "review": review, "path": result_path}
        except Exception:
            pass

    base = load_agent_llm_config()
    config = AgentLLMConfig(
        provider=base.provider,
        model=reviewer_model,
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
        category_name_zh=category_name_zh,
        reviewer_model_id=reviewer_model,
        candidate_uid=record.candidate_uid,
        known_duplicate_case_ids=duplicate_ids,
    )
    request_path = case_dir / "request.json"
    response_path = case_dir / "response.json"
    try:
        raw = client.generate_with_system(
            SYSTEM_MESSAGE,
            user_message,
            response_format={"type": "json_object"},
            temperature=float(profile["temperature"]),
            top_p=float(profile["top_p"]),
            max_completion_tokens=int(profile["max_completion_tokens"]),
            reasoning_effort=profile.get("reasoning_effort"),
        )
        request_path.write_text(
            json.dumps(client.last_request_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        response_path.write_text(
            json.dumps(client.last_response_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        review = SemanticReview.model_validate(json.loads(raw))
        if review.case_id != record.case.case_id:
            raise ValueError("审核结果 case_id 与输入不一致")
        if review.candidate_uid != record.candidate_uid:
            raise ValueError("审核结果 candidate_uid 与输入不一致")
        if review.reviewer_model_id != reviewer_model:
            raise ValueError("审核结果 reviewer_model_id 与实际审核模型不一致")
        result_path.write_text(review.model_dump_json(indent=2), encoding="utf-8")
        metadata = {
            "status": "COMPLETED",
            "candidate_uid": record.candidate_uid,
            "case_id": record.case.case_id,
            "generator_model_id": record.generator_model_id,
            "reviewer_model_id": reviewer_model,
            "decision": review.decision,
            "usage": client.last_usage,
            "latency_ms": client.last_latency_ms,
            "response_metadata": client.last_response_metadata,
        }
        (case_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return {"status": "COMPLETED", "review": review, "path": result_path}
    except Exception as exc:
        if client.last_request_payload:
            request_path.write_text(
                json.dumps(client.last_request_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        if client.last_response_payload is not None:
            response_path.write_text(
                json.dumps(client.last_response_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        (case_dir / "error.json").write_text(
            json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return {
            "status": "FAILED",
            "candidate_uid": record.candidate_uid,
            "case_id": record.case.case_id,
            "reviewer_model_id": reviewer_model,
            "error": str(exc),
            "path": case_dir / "error.json",
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--candidate-root", type=Path,
        default=ROOT / "data" / "candidate_batches" / "批量生成-第01轮",
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=ROOT / "data" / "candidate_reviews" / "试审-第01轮",
    )
    parser.add_argument("--per-item", type=int, default=2)
    parser.add_argument("--reviewers-per-case", type=int, default=2)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    records = discover_candidates(args.candidate_root)
    deterministic, duplicates = audit_candidates(records)
    selected = select_sample(records, args.per_item)
    profiles = load_profiles()
    reviewer_ids = sorted(profiles)
    catalog = load_evaluation_catalog()
    category_names = {item.code: item.name_zh for item in catalog.categories}
    duplicate_map: dict[str, list[str]] = defaultdict(list)
    for pair in duplicates:
        if pair["kind"] == "DUPLICATE_CASE_ID":
            for uid in pair["candidate_uids"]:
                duplicate_map[uid].extend(
                    other for other in pair["candidate_uids"] if other != uid
                )
            continue
        duplicate_map[pair["candidate_uid_a"]].append(pair["candidate_uid_b"])
        duplicate_map[pair["candidate_uid_b"]].append(pair["candidate_uid_a"])
    deterministic_map = {item.candidate_uid: item for item in deterministic}

    manifest_cases = []
    jobs = []
    for record in selected:
        assigned = assign_reviewers(record, reviewer_ids, args.reviewers_per_case)
        manifest_cases.append({
            "candidate_uid": record.candidate_uid,
            "case_id": record.case.case_id,
            "item_name": record.item_name,
            "category_name_zh": category_names[record.case.category],
            "generator_model_id": record.generator_model_id,
            "source_path": str(record.source_path),
            "deterministic_passed": deterministic_map[record.candidate_uid].passed,
            "reviewer_model_ids": assigned,
        })
        for reviewer in assigned:
            jobs.append((record, reviewer))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": "candidate_review_sample_manifest_v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "selection": {
            "per_item": args.per_item,
            "item_count": len({record.item_name for record in selected}),
        },
        "reviewers_per_case": args.reviewers_per_case,
        "case_count": len(selected),
        "review_job_count": len(jobs),
        "cases": manifest_cases,
    }
    (args.output_dir / "sample_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if args.dry_run:
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                review_one,
                record,
                reviewer,
                profiles[reviewer],
                args.output_dir,
                category_names[record.case.category],
                duplicate_map[record.candidate_uid],
            ): (record, reviewer)
            for record, reviewer in jobs
        }
        for future in concurrent.futures.as_completed(futures):
            record, reviewer = futures[future]
            result = future.result()
            results.append(result)
            print(json.dumps({
                "status": result["status"],
                "case_id": record.case.case_id,
                "reviewer_model_id": reviewer,
                "decision": getattr(result.get("review"), "decision", None),
                "error": result.get("error"),
            }, ensure_ascii=False), flush=True)

    completed = [item for item in results if item["status"] in {"COMPLETED", "SKIPPED"}]
    decisions = Counter(item["review"].decision for item in completed)
    per_case = defaultdict(list)
    reviews_by_uid = defaultdict(list)
    for item in completed:
        per_case[item["review"].candidate_uid].append(item["review"].decision)
        reviews_by_uid[item["review"].candidate_uid].append(item["review"])
    disagreements = {
        case_id: values for case_id, values in per_case.items() if len(set(values)) > 1
    }
    consensus_cases = []
    consensus_counts = Counter()
    for manifest_case in manifest_cases:
        uid = manifest_case["candidate_uid"]
        case_reviews = reviews_by_uid.get(uid, [])
        case_decisions = [review.decision for review in case_reviews]
        if len(case_decisions) != args.reviewers_per_case:
            consensus = "INCOMPLETE"
        elif len(set(case_decisions)) > 1:
            consensus = "DISAGREEMENT"
        else:
            consensus = f"UNANIMOUS_{case_decisions[0]}"
        consensus_counts[consensus] += 1
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
    consensus_path = args.output_dir / "case_consensus.json"
    consensus_path.write_text(
        json.dumps(
            {
                "schema_version": "candidate_case_consensus_v1",
                "cases": consensus_cases,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    summary = {
        "schema_version": "candidate_semantic_review_summary_v1",
        "completed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "sample_case_count": len(selected),
        "review_job_count": len(jobs),
        "completed_review_count": len(completed),
        "failed_review_count": len(results) - len(completed),
        "decision_counts": dict(decisions),
        "consensus_counts": dict(consensus_counts),
        "disagreement_case_count": len(disagreements),
        "disagreements": disagreements,
        "case_consensus_path": str(consensus_path),
    }
    (args.output_dir / "semantic_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(completed) == len(jobs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
