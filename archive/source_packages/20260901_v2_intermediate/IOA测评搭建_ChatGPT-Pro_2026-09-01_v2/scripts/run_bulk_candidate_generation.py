#!/usr/bin/env python
"""Generate five candidates per evaluation item and configured model.

The script is resumable: an existing successful ``expanded_cases.jsonl`` is
skipped, while every attempt remains in the active batch directory.  The two
trust/authorization variants are treated as separate evaluation items.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
GENERATOR = ROOT / "scripts" / "generate_candidate_batch.py"
PROFILE_PATH = ROOT / "config" / "generation_model_profiles.yaml"
ITEMS = [
    ("跨系统级联扩散", None), ("生态错误共识", None), ("谣言扩散", None),
    ("规范漂移", None), ("激励错配", None), ("判断让渡", None),
    ("讨论扭曲", None), ("能动性弱化", None),
    ("信任与授权链", "drift"), ("信任与授权链", "vague"),
    ("资格申报", None),
]


def configured_models(selected: list[str] | None = None) -> list[tuple[str, dict]]:
    payload = yaml.safe_load(PROFILE_PATH.read_text(encoding="utf-8"))
    enabled = {
        name: cfg
        for name, cfg in payload.get("models", {}).items()
        if cfg.get("enabled")
    }
    if not selected:
        return list(enabled.items())
    unknown = [name for name in selected if name not in enabled]
    if unknown:
        raise ValueError(f"models are not enabled or configured: {unknown}")
    return [(name, enabled[name]) for name in selected]


def run_one(model: str, category: str, variant: str | None, ordinal: int,
            seed: int, timeout: int, output_root: Path,
            max_completion_tokens: int, repair_attempts: int) -> dict:
    label = variant or "default"
    batch_id = f"{category}__{label}__第{ordinal:02d}条"
    evidence = output_root / batch_id / model.replace("/", "_")
    if (evidence / "expanded_cases.jsonl").exists():
        return {"status": "SKIPPED", "model": model, "category": category,
                "variant": variant, "ordinal": ordinal, "batch_id": batch_id}
    cmd = [str(PYTHON), str(GENERATOR), "--category", category,
           "--model", model, "--batch-id", batch_id, "--seed", str(seed),
           "--timeout", str(timeout), "--retry-count", "0",
           "--repair-attempts", str(repair_attempts),
           "--max-completion-tokens", str(max_completion_tokens),
           "--output-root", str(output_root)]
    if variant:
        cmd.extend(["--variant", variant])
    started = datetime.now().isoformat(timespec="seconds")
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout * (repair_attempts + 1) + 120)
    except subprocess.TimeoutExpired:
        return {"status": "DRIVER_TIMEOUT", "model": model, "category": category,
                "variant": variant, "ordinal": ordinal, "batch_id": batch_id,
                "started_at": started}
    if (evidence / "expanded_cases.jsonl").exists():
        status = "EXPANDED"
    elif (evidence / "candidate_batch.json").exists():
        status = "INVALID_BATCH"
    elif (evidence / "response_raw.json").exists():
        status = "CALL_FAILED"
    else:
        status = "FAILED"
    return {"status": status, "model": model, "category": category,
            "variant": variant, "ordinal": ordinal, "batch_id": batch_id,
            "returncode": proc.returncode, "started_at": started,
            "stdout_tail": (proc.stdout or "")[-3000:],
            "stderr_tail": (proc.stderr or "")[-1000:]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--seed", type=int, default=2026082903)
    ap.add_argument("--max-completion-tokens", type=int, default=16384)
    ap.add_argument("--repair-attempts", type=int, default=1)
    ap.add_argument(
        "--models", nargs="+", default=None,
        help="只运行指定且已启用的生成模型；省略时运行全部已启用模型",
    )
    ap.add_argument("--output-root", type=Path,
                    default=ROOT / "data" / "candidate_batches" / "批量生成-第01轮")
    args = ap.parse_args()
    models = configured_models(args.models)
    jobs = [(model, category, variant, ordinal)
            for model, _cfg in models for category, variant in ITEMS
            for ordinal in range(1, args.repeats + 1)]
    args.output_root.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(run_one, model, category, variant, ordinal,
                                args.seed + ordinal, args.timeout, args.output_root,
                                args.max_completion_tokens, args.repair_attempts)
                   for model, category, variant, ordinal in jobs]
        for f in concurrent.futures.as_completed(futures):
            result = f.result(); results.append(result)
            print(json.dumps({k: result.get(k) for k in
                              ("status", "model", "category", "variant", "ordinal")},
                             ensure_ascii=False), flush=True)
    results.sort(key=lambda x: (x["model"], x["category"], x.get("variant") or "", x["ordinal"]))
    summary = {"started_at": datetime.now().isoformat(timespec="seconds"),
               "repeats": args.repeats, "item_count": len(ITEMS),
               "model_count": len(models), "job_count": len(jobs),
               "models": [m for m, _ in models], "results": results}
    path = args.output_root / "bulk_summary.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": "SUMMARY_WRITTEN", "path": str(path),
                      "job_count": len(jobs)}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
