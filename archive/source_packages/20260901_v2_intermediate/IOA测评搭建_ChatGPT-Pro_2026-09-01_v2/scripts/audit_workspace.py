#!/usr/bin/env python
"""Validate the current ten-item workspace without provider calls."""

from __future__ import annotations

import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.cases import get_business_cases  # noqa: E402
from src.evaluation.business_protocol.validation import validate_case_catalog  # noqa: E402
from src.evaluation.catalog import load_evaluation_catalog  # noqa: E402


CANONICAL_PROMPT = PROJECT_ROOT / "docs" / "十项测评场景生成Prompt_作者版v3.md"
ARCHIVE_ROOT = PROJECT_ROOT / "archive"


def main() -> int:
    checks: dict[str, bool] = {}
    details: dict[str, object] = {}

    catalog = load_evaluation_catalog()
    checks["catalog_has_ten_categories"] = len(catalog.categories) == 10
    details["category_names"] = [item.name_zh for item in catalog.categories]

    protocol_cases = get_business_cases()
    validate_case_catalog(protocol_cases)
    checks["protocol_matches_catalog"] = (
        set(catalog.protocol_case_ids) == set(protocol_cases)
    )
    details["protocol_case_count"] = len(protocol_cases)
    details["protocol_step_count"] = sum(
        len(case.steps) + len(case.recovery_steps)
        for case in protocol_cases.values()
    )

    checks["canonical_prompt_exists"] = CANONICAL_PROMPT.is_file()
    checks["legacy_runner_absent_from_mainline"] = not (
        PROJECT_ROOT / "scripts" / "run_agent_model_suite.py"
    ).exists()
    checks["legacy_dataset_absent_from_mainline"] = not (
        PROJECT_ROOT / "data" / "agent_model_cases"
    ).exists()
    checks["archive_documented"] = (ARCHIVE_ROOT / "README.md").is_file()

    current_test_files = sorted((PROJECT_ROOT / "tests").glob("test_*.py"))
    details["current_test_file_count"] = len(current_test_files)
    details["canonical_prompt"] = str(CANONICAL_PROMPT.relative_to(PROJECT_ROOT))
    details["archive_root"] = str(ARCHIVE_ROOT.relative_to(PROJECT_ROOT))

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
