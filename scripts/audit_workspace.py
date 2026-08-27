#!/usr/bin/env python
"""Validate the canonical ten-item workspace layout without provider calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.agent_model.case_loader import CaseLoader  # noqa: E402
from src.evaluation.agent_model.dataset_bundle import (  # noqa: E402
    CASE_DIR,
    CATEGORY_FILES,
    GENERATED_DATASET,
    assert_generated_dataset_current,
)
from src.evaluation.business_protocol.cases import get_business_cases  # noqa: E402
from src.evaluation.business_protocol.validation import (  # noqa: E402
    validate_case_catalog,
)
from src.evaluation.catalog import load_evaluation_catalog  # noqa: E402


CANONICAL_PROMPT = (
    PROJECT_ROOT / "docs" / "当前方案" / "十项测评场景扩增生成Prompt.md"
)


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    catalog = load_evaluation_catalog()
    checks["catalog_has_ten_categories"] = len(catalog.categories) == 10
    details["category_codes"] = list(catalog.category_codes)

    protocol_cases = get_business_cases()
    validate_case_catalog(protocol_cases)
    checks["protocol_matches_catalog"] = (
        set(catalog.protocol_case_ids) == set(protocol_cases)
    )
    details["protocol_case_count"] = len(protocol_cases)

    assert_generated_dataset_current()
    report = CaseLoader(GENERATED_DATASET).validate_only()
    checks["legacy_dataset_valid"] = report["valid_cases"] == 160 and not report["errors"]
    details["legacy_dataset"] = {
        "coverage": "8_of_10_categories",
        "valid_cases": report["valid_cases"],
        "by_category": report["by_category"],
        "held_out": report["split_summary"].get("held_out", 0),
    }

    checks["compact_defaults_present"] = (
        CASE_DIR / "_shared_defaults.json"
    ).is_file()
    checks["all_category_files_present"] = all(
        (CASE_DIR / name).is_file() for name in CATEGORY_FILES
    )
    current_prompts = list(
        (PROJECT_ROOT / "docs" / "当前方案").glob("*测评*Prompt*.md")
    )
    checks["canonical_prompt_is_unique"] = current_prompts == [CANONICAL_PROMPT]
    details["canonical_prompt"] = str(CANONICAL_PROMPT.relative_to(PROJECT_ROOT))

    failed = [name for name, passed in checks.items() if not passed]
    print(json.dumps({
        "status": "VALID" if not failed else "INVALID",
        "canonical_track": catalog.canonical_track,
        "checks": checks,
        "details": details,
        "failed_checks": failed,
        "provider_calls": 0,
    }, ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
