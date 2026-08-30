#!/usr/bin/env python
"""Run deterministic quality checks over all generated candidates."""

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

from src.evaluation.candidate_review import audit_candidates, discover_candidates  # noqa: E402


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
        default=ROOT / "data" / "candidate_reviews" / "试审-第01轮",
    )
    args = parser.parse_args()

    records = discover_candidates(args.candidate_root)
    if not records:
        raise SystemExit(f"没有找到候选数据：{args.candidate_root}")
    reviews, duplicates = audit_candidates(records)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    review_path = args.output_dir / "deterministic_reviews.jsonl"
    review_path.write_text(
        "\n".join(item.model_dump_json() for item in reviews) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "duplicate_pairs.json").write_text(
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
        "candidate_root": str(args.candidate_root),
        "candidate_count": len(records),
        "passed_count": sum(item.passed for item in reviews),
        "failed_count": sum(not item.passed for item in reviews),
        "duplicate_pair_count": len(duplicates),
        "finding_counts": [
            {"severity": severity, "code": code, "count": count}
            for (severity, code), count in sorted(finding_counts.items())
        ],
        "artifacts": {
            "reviews": str(review_path),
            "duplicates": str(args.output_dir / "duplicate_pairs.json"),
        },
    }
    summary_path = args.output_dir / "deterministic_summary.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
