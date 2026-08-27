#!/usr/bin/env python
"""Build or verify the combined Agent-model dataset from canonical case files."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.agent_model.dataset_bundle import (  # noqa: E402
    CASE_DIR,
    GENERATED_DATASET,
    assert_generated_dataset_current,
    build_dataset_bytes,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the generated 160-case JSONL from eight canonical files."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only verify that the generated file matches the canonical files.",
    )
    args = parser.parse_args()

    if args.check:
        assert_generated_dataset_current()
        print(f"OK: {GENERATED_DATASET.relative_to(PROJECT_ROOT)} is current")
        return 0

    GENERATED_DATASET.parent.mkdir(parents=True, exist_ok=True)
    GENERATED_DATASET.write_bytes(build_dataset_bytes(CASE_DIR))
    print(f"Built: {GENERATED_DATASET.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
