#!/usr/bin/env python
"""Run the five-model pilot generation round sequentially.

Drives scripts/generate_candidate_batch.py once per model with per-model
reasoning effort and timeout, then writes a summary of every attempt to
``data/candidate_batches/<batch_id>/pilot_summary.json``.  Models whose
candidate directory already contains ``expanded_cases.jsonl`` are skipped
unless ``--force`` is given, so an interrupted round can be resumed.

Per-model notes come from the 2026-08-28 probe and Codex pilot evidence:

- ``glm-5.3-flash`` only accepts low/high/max ("始终思考" models reject
  none/minimal/medium).
- ``deepseek-v4-pro`` at ``max`` thought for over 15 minutes without
  returning; use ``high`` for DeepSeek models until further evidence.
- Long requests on the gateway can be cut off; every model runs
  sequentially with its own timeout so one stall cannot block the rest.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GENERATOR = PROJECT_ROOT / "scripts" / "generate_candidate_batch.py"

PILOT_MODELS = [
    {"model": "gpt-5.6-sol", "reasoning_effort": "max", "timeout": 600},
    {
        "model": "deepseek-v4-pro-0813",
        "reasoning_effort": "high",
        "timeout": 900,
        "note": "max 档实测思考超过 15 分钟未返回，暂用 high",
    },
    {"model": "claude-opus-5", "reasoning_effort": "max", "timeout": 600},
    {
        "model": "glm-5.3-flash",
        "reasoning_effort": "max",
        "timeout": 600,
        "note": "仅支持 low/high/max",
    },
    {"model": "qwen3.8-flash", "reasoning_effort": "max", "timeout": 600},
]


def run_one(entry: dict, *, category: str, batch_id: str, seed: int) -> dict:
    command = [
        str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"),
        str(GENERATOR),
        "--category", category,
        "--model", entry["model"],
        "--batch-id", batch_id,
        "--seed", str(seed),
        "--reasoning-effort", entry["reasoning_effort"],
        "--timeout", str(entry["timeout"]),
    ]
    started = datetime.now().isoformat(timespec="seconds")
    try:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=entry["timeout"] * 2 + 120,
        )
    except subprocess.TimeoutExpired as exc:
        return {"model": entry["model"], "status": "DRIVER_TIMEOUT", "started_at": started}
    lines = [
        line
        for line in (completed.stdout or "").splitlines()
        if line.strip().startswith("{")
    ]
    result: dict = {}
    for line in lines:
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
    if completed.returncode != 0:
        result["status"] = result.get("status", "FAILED")
        result["returncode"] = completed.returncode
    result["model"] = entry["model"]
    result["reasoning_effort"] = entry["reasoning_effort"]
    result["started_at"] = started
    if entry.get("note"):
        result["note"] = entry["note"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--category", default="规范漂移")
    parser.add_argument("--batch-id", default="规范漂移-五模型试点-第01批")
    parser.add_argument("--seed", type=int, default=2026082802)
    parser.add_argument("--force", action="store_true", help="重跑已有成功产物的模型")
    args = parser.parse_args()

    batch_dir = PROJECT_ROOT / "data" / "candidate_batches" / args.batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    summary_path = batch_dir / "pilot_summary.json"

    previous: list = []
    if summary_path.exists():
        previous = json.loads(summary_path.read_text(encoding="utf-8")).get("results", [])
    done = {
        item.get("model")
        for item in previous
        if item.get("status") == "EXPANDED"
    }

    results = [item for item in previous if item.get("model") in done]
    for entry in PILOT_MODELS:
        if entry["model"] in done and not args.force:
            print(json.dumps({"status": "SKIPPED", "model": entry["model"]}, ensure_ascii=False))
            continue
        print(json.dumps({"status": "RUNNING", "model": entry["model"]}, ensure_ascii=False))
        results.append(run_one(entry, category=args.category, batch_id=args.batch_id, seed=args.seed))

    summary = {
        "batch_id": args.batch_id,
        "category": args.category,
        "seed": args.seed,
        "written_at": datetime.now().isoformat(timespec="seconds"),
        "results": results,
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({"status": "SUMMARY_WRITTEN", "path": str(summary_path)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
