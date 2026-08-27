#!/usr/bin/env python
"""Create compact authoring files from the complete eight-category dataset."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.agent_model.case_loader import CaseLoader  # noqa: E402
from src.evaluation.agent_model.dataset_authoring import (  # noqa: E402
    AUTHORING_FORMAT,
    DEFAULTS_FILE_NAME,
    compact_case_dict,
)
from src.evaluation.agent_model.dataset_bundle import (  # noqa: E402
    CASE_DIR,
    CATEGORY_FILES,
    GENERATED_DATASET,
)
from src.evaluation.agent_model.models import CATEGORY_TO_RISK  # noqa: E402


GLOBAL_DEFAULT_FIELDS = (
    "schema_version",
    "model_config",
    "data_policy",
    "visibility_policy",
    "context_policy",
    "result_layers",
)
CATEGORY_DEFAULT_CANDIDATES = (
    "category",
    "risk_type",
    "execution_config",
    "expected_safe_behavior",
    "risky_behavior",
    "objective_metrics",
    "judge_focus",
    "roles",
)


def _constant_value(rows: list[dict[str, Any]], field: str) -> Any | None:
    values = [row.get(field) for row in rows]
    first = values[0]
    return first if all(value == first for value in values) else None


def build_defaults(rows: list[dict[str, Any]]) -> dict[str, Any]:
    global_defaults = {
        field: _constant_value(rows, field)
        for field in GLOBAL_DEFAULT_FIELDS
    }
    if any(value is None for value in global_defaults.values()):
        raise ValueError("one or more required global defaults are not constant")

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[row["category_code"]].append(row)

    category_defaults: dict[str, dict[str, Any]] = {}
    for code in CATEGORY_TO_RISK:
        category_rows = by_category[code]
        defaults: dict[str, Any] = {}
        for field in CATEGORY_DEFAULT_CANDIDATES:
            value = _constant_value(category_rows, field)
            if value is not None:
                defaults[field] = value
        category_defaults[code] = defaults

    return {
        "authoring_format": AUTHORING_FORMAT,
        "expanded_schema_version": "2.0",
        "description": (
            "Category JSONL rows store only per-case differences. "
            "CaseLoader merges these defaults before Pydantic validation."
        ),
        "global_defaults": global_defaults,
        "category_defaults": category_defaults,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compact the eight-category authoring dataset without changing runtime objects."
    )
    parser.add_argument("--source", type=Path, default=GENERATED_DATASET)
    parser.add_argument("--output-dir", type=Path, default=CASE_DIR)
    args = parser.parse_args()

    source_loader = CaseLoader(args.source)
    rows = source_loader.expanded_dicts()
    if len(rows) != 160:
        raise SystemExit(f"expected 160 expanded source rows, got {len(rows)}")
    defaults = build_defaults(rows)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    defaults_path = args.output_dir / DEFAULTS_FILE_NAME
    defaults_path.write_text(
        json.dumps(defaults, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_category[row["category_code"]].append(row)

    for filename in CATEGORY_FILES:
        code = filename.split("_", 1)[0]
        compact_rows = [compact_case_dict(row, defaults) for row in by_category[code]]
        (args.output_dir / filename).write_text(
            "".join(
                json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
                for row in compact_rows
            ),
            encoding="utf-8",
        )

    print(json.dumps({
        "status": "COMPACTED",
        "source_rows": len(rows),
        "category_files": len(CATEGORY_FILES),
        "defaults_file": str(defaults_path),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
