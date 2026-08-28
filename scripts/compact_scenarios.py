#!/usr/bin/env python
"""Convert full scenario JSON/JSONL files to compact authoring JSONL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.loader import load_business_cases_from_paths
from src.evaluation.scenario_generation.compact import compact_envelope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path, nargs="+")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths: list[Path] = []
    for item in args.input:
        if any(char in str(item) for char in "*?["):
            paths.extend(sorted(Path().glob(str(item))))
        elif item.is_dir():
            paths.extend(sorted(item.glob("*.jsonl")))
        else:
            paths.append(item)
    cases = load_business_cases_from_paths(paths)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for case in cases.values():
            handle.write(json.dumps(compact_envelope(case), ensure_ascii=False, separators=(",", ":")) + "\n")
    print(json.dumps({"status": "COMPACTED", "case_count": len(cases), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
