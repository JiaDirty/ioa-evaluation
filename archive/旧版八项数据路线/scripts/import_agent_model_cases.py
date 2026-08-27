#!/usr/bin/env python
"""Import and validate the v2 JSONL dataset, split into per-category files.

Usage:
    python scripts/import_agent_model_cases.py --input <path> [--validate-only]
    python scripts/import_agent_model_cases.py --input <path> --output-dir data/agent_model_cases

The input should be the 160-line v2 JSONL file:
    IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure src/ is on sys.path
_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.models import CATEGORY_TO_RISK


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import and validate IoA Agent Model Safety Evaluation dataset (v2)."
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="Path to the v2 JSONL dataset file.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="data/agent_model_cases",
        help="Output directory for per-category JSONL files (default: data/agent_model_cases).",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Only validate; do not split into per-category files.",
    )
    parser.add_argument(
        "--report", "-r",
        default="data_validation_report.json",
        help="Path for the validation report JSON (default: data_validation_report.json).",
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: Input file not found: {input_path}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    report_path = Path(args.report)

    print(f"Loading dataset: {input_path}")
    loader = CaseLoader(input_path)

    if args.validate_only:
        print("Running --validate-only ...")
        report = loader.validate_only()
    else:
        print(f"Splitting to: {output_dir}")
        loader.split_to_files(output_dir)
        report = loader.validate_only()

    # Write report
    n_err = report.get("errors", 0)
    report.setdefault("input_file", str(input_path))
    report.setdefault("schema_version", "2.0")
    report.setdefault("category_counts", report.pop("by_category", {}))
    report.setdefault("per_category_expected", {
        code: 20 for code in CATEGORY_TO_RISK
    })
    report.setdefault("error_count", n_err)
    report.setdefault("error_details", [
        {"case_id": e.case_id, "line": e.line_number, "message": str(e)}
        for e in loader.errors
    ])
    # Add category-specific checks
    for code, count in report.get("category_counts", {}).items():
        if count != 20:
            report.setdefault("warnings", []).append(
                f"{code}: expected 20 cases, found {count}"
            )

    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
    print(f"Validation report written to: {report_path}")

    # Summary
    n_valid = report["valid_cases"]
    n_err = report["error_count"]
    print(f"\n{'='*60}")
    print(f"  Total valid cases: {n_valid}")
    print(f"  Validation errors: {n_err}")
    if report.get("warnings"):
        print(f"  Warnings: {len(report['warnings'])}")
    for code, count in report.get("category_counts", {}).items():
        flag = " ✓" if count == 20 else " ⚠"
        print(f"  {code}: {count} cases{flag}")
    print(f"{'='*60}")

    if n_err > 0:
        print(f"\nERROR DETAILS:")
        for err in report["error_details"][:10]:
            print(f"  {err}")
        if len(report["error_details"]) > 10:
            print(f"  ... and {len(report['error_details']) - 10} more errors")
        return 1

    if n_valid != 160:
        print(f"\nWARNING: Expected 160 valid cases, got {n_valid}", file=sys.stderr)
        return 1

    print("\n✓ All 160 cases passed validation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
