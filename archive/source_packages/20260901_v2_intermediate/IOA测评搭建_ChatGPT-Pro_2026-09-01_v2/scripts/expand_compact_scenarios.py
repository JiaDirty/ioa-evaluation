#!/usr/bin/env python
"""Expand compact authoring JSONL and validate every resulting case."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.evaluation.business_protocol.loader import load_business_cases_from_paths
from src.evaluation.scenario_generation.compact import expand_envelope


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    expanded = []
    raw_text = args.input.read_text(encoding="utf-8")
    try:
        parsed = json.loads(raw_text)
        if isinstance(parsed, dict) and isinstance(parsed.get("cases"), list):
            payloads = [
                {"schema_version": "business_case_authoring_v1", "case": case}
                for case in parsed["cases"]
            ]
        elif isinstance(parsed, dict):
            payloads = [parsed]
        elif isinstance(parsed, list):
            payloads = parsed
        else:
            raise ValueError("input must be a compact batch object, array, or JSONL")
        for payload in payloads:
            expanded.append(expand_envelope(payload))
    except json.JSONDecodeError:
        for line_number, line in enumerate(raw_text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                expanded.append(expand_envelope(json.loads(line)))
            except Exception as exc:
                print(json.dumps({"status": "INVALID", "line": line_number, "error": str(exc)}, ensure_ascii=False))
                return 1
    except Exception as exc:
        print(json.dumps({"status": "INVALID", "error": str(exc)}, ensure_ascii=False))
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="\n") as handle:
        for case in expanded:
            handle.write(json.dumps({"schema_version": "business_case_spec_v1", "case": case.model_dump(mode="json")}, ensure_ascii=False, separators=(",", ":")) + "\n")
    # Re-read through the official loader to enforce duplicate IDs and file shape.
    load_business_cases_from_paths([args.output])
    print(json.dumps({"status": "EXPANDED", "case_count": len(expanded), "output": str(args.output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
