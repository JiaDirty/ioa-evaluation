#!/usr/bin/env python
"""Run every configured reasoning effort for every candidate model.

Each model/effort pair is an independent one-case generation.  The existing
generator keeps the complete request/response evidence and writes only under
data/candidate_batches; this driver adds a machine-readable matrix summary.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
GENERATOR = ROOT / "scripts" / "generate_candidate_batch.py"

MODELS = [
    "gpt-5.6-luna",
    "deepseek-v4-flash",
    "gemini-3.7-flash",
    "claude-haiku-4-5",
    "gpt-5.6-sol",
    "claude-opus-5",
    "glm-5.3-flash",
    "deepseek-v4-pro-0813",
    "qwen3.8-flash",
]
EFFORTS = ["none", "minimal", "low", "medium", "high", "max"]


def run_one(model: str, effort: str, category: str, seed: int, timeout: int, root: Path) -> dict:
    batch_id = f"{category}-思考档位矩阵-{model}-{effort}"
    command = [
        str(PYTHON), str(GENERATOR), "--category", category,
        "--model", model, "--batch-id", batch_id, "--seed", str(seed),
        "--reasoning-effort", effort, "--timeout", str(timeout),
        "--retry-count", "0", "--output-root", str(root),
    ]
    started = datetime.now().isoformat(timespec="seconds")
    try:
        completed = subprocess.run(
            command, cwd=ROOT, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout + 120,
        )
    except subprocess.TimeoutExpired:
        return {"model": model, "reasoning_effort": effort, "status": "DRIVER_TIMEOUT", "batch_id": batch_id, "started_at": started}
    parsed: dict = {}
    for line in (completed.stdout or "").splitlines():
        if line.lstrip().startswith("{"):
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "status" in item:
                parsed = item
    evidence_dir = root / batch_id / model.replace("/", "_")
    # The generator's final result is pretty-printed, so its status is not
    # necessarily on the same line as the opening JSON brace.  Prefer the
    # persisted evidence, which is also what downstream review consumes.
    if (evidence_dir / "expanded_cases.jsonl").exists():
        parsed["status"] = "EXPANDED"
    elif (evidence_dir / "candidate_batch.json").exists():
        parsed["status"] = "INVALID_BATCH"
    elif (evidence_dir / "response_raw.json").exists() and parsed.get("status") == "CALLING":
        parsed["status"] = "CALL_FAILED"
    parsed.update({"model": model, "reasoning_effort": effort, "batch_id": batch_id, "started_at": started, "returncode": completed.returncode})
    if completed.stderr:
        parsed["stderr_tail"] = completed.stderr[-2000:]
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--category", default="规范漂移")
    parser.add_argument("--seed", type=int, default=2026082901)
    parser.add_argument("--timeout", type=int, default=600)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data" / "candidate_batches")
    parser.add_argument("--models", nargs="*", default=MODELS)
    parser.add_argument("--efforts", nargs="*", default=EFFORTS)
    args = parser.parse_args()
    jobs = [(m, e) for m in args.models for e in args.efforts]
    out_dir = args.output_root / f"{args.category}-思考档位矩阵"
    out_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, m, e, args.category, args.seed, args.timeout, args.output_root): (m, e) for m, e in jobs}
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            results.append(result)
            print(json.dumps({k: result.get(k) for k in ("model", "reasoning_effort", "status", "returncode")}, ensure_ascii=False), flush=True)
    results.sort(key=lambda x: (x.get("model", ""), EFFORTS.index(x.get("reasoning_effort")) if x.get("reasoning_effort") in EFFORTS else 99))
    summary = {"category": args.category, "seed": args.seed, "models": args.models, "reasoning_efforts": args.efforts, "job_count": len(jobs), "results": results, "written_at": datetime.now().isoformat(timespec="seconds")}
    path = out_dir / "matrix_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "SUMMARY_WRITTEN", "path": str(path), "job_count": len(jobs)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
