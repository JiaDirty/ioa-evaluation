#!/usr/bin/env python
"""Export the hand-authored regression cases to external JSONL files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.cases import get_builtin_business_cases


CASE_FILENAMES = {
    "travel-scope-001": "01_跨系统级联扩散.jsonl",
    "batch-consensus-001": "02_生态错误共识.jsonl",
    "station-rumor-001": "03_谣言扩散.jsonl",
    "purchase-norm-001": "04_规范漂移.jsonl",
    "invoice-incentive-001": "05_激励错配.jsonl",
    "travel-delegation-001": "06_判断让渡.jsonl",
    "discussion-distortion-001": "07_讨论扭曲.jsonl",
    "finance-agency-001": "08_能动性弱化.jsonl",
    "med-auth-drift-001": "09_授权转述漂移.jsonl",
    "med-auth-vague-001": "10_含糊授权越界.jsonl",
    "clin-task-declare-001": "11_资格申报.jsonl",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export built-in business cases as one enveloped JSONL record per file."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_ROOT / "data" / "scenarios",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    cases = get_builtin_business_cases()
    missing = sorted(set(cases) ^ set(CASE_FILENAMES))
    if missing:
        raise SystemExit(f"case filename map is out of date: {missing}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    for case_id, filename in CASE_FILENAMES.items():
        payload = {
            "schema_version": "business_case_spec_v1",
            "case": cases[case_id].model_dump(mode="json"),
        }
        (args.output_dir / filename).write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    print(json.dumps({"status": "EXPORTED", "file_count": len(CASE_FILENAMES)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
