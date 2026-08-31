#!/usr/bin/env python
"""Run the expandable blueprint generator for a resumable model/category matrix."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "candidate_batches" / "批量生成-第10轮-蓝图v9.2全量矩阵"
MODELS = (
    "gpt-5.6-luna",
    "deepseek-v4-flash",
    "gemini-3.7-flash",
    "gpt-5.6-sol",
    "claude-opus-5",
    "glm-5.3-flash",
    "deepseek-v4-pro-0813",
    "qwen3.8-flash",
)
CATEGORIES = (
    ("跨系统级联扩散", None),
    ("生态错误共识", None),
    ("谣言扩散", None),
    ("规范漂移", None),
    ("激励错配", None),
    ("判断让渡", None),
    ("讨论扭曲", None),
    ("能动性弱化", None),
    ("信任与授权链", "drift"),
    ("信任与授权链", "vague"),
    ("资格申报", None),
)


def _job_key(category: str, variant: str | None, model: str, number: int) -> str:
    suffix = variant or "default"
    return f"{category}::{suffix}::{model}::{number:02d}"


def _run_job(
    *,
    output: Path,
    category: str,
    variant: str | None,
    model: str,
    number: int,
    max_completion_tokens: int,
    repair_attempts: int,
    prompt_path: Path,
) -> dict[str, object]:
    batch_id = f"{category}__{variant or 'default'}__第{number:02d}条"
    command = [
        sys.executable,
        str(ROOT / "scripts" / "generate_candidate_batch.py"),
        "--category",
        category,
        "--model",
        model,
        "--batch-id",
        batch_id,
        "--output-root",
        str(output),
        "--repair-attempts",
        str(repair_attempts),
        "--max-completion-tokens",
        str(max_completion_tokens),
        "--prompt-path",
        str(prompt_path),
    ]
    if variant:
        command.extend(["--variant", variant])
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    job_dir = output / batch_id / model
    log_path = job_dir / "matrix_run.log"
    job_dir.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        completed.stdout + ("\n" + completed.stderr if completed.stderr else ""),
        encoding="utf-8",
    )
    result = {
        "job_key": _job_key(category, variant, model, number),
        "category": category,
        "variant": variant,
        "model": model,
        "number": number,
        "batch_id": batch_id,
        "exit_code": completed.returncode,
        "status": "COMPLETED" if completed.returncode == 0 else "FAILED",
        "started_at": started,
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "log_path": str(log_path),
    }
    (job_dir / "matrix_job_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--count", type=int, default=5, help="每个模型和逻辑测评项的候选数")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--max-completion-tokens", type=int, default=32768)
    parser.add_argument("--repair-attempts", type=int, default=2)
    parser.add_argument(
        "--prompt-path",
        type=Path,
        default=ROOT / "docs" / "十项测评场景生成Prompt_蓝图版v5.md",
        help="覆盖默认场景生成 Prompt，并写入每个任务的生成证据。",
    )
    parser.add_argument("--model", action="append", dest="models")
    parser.add_argument("--category", action="append", dest="categories")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.count < 1 or args.workers < 1:
        raise SystemExit("count and workers must be positive")
    models = tuple(args.models or MODELS)
    category_filter = set(args.categories or [])
    categories = tuple(
        item for item in CATEGORIES if not category_filter or item[0] in category_filter
    )
    jobs = [
        (category, variant, model, number)
        for category, variant in categories
        for model in models
        for number in range(1, args.count + 1)
    ]
    manifest_path = args.output / "matrix_manifest.json"
    args.output.mkdir(parents=True, exist_ok=True)
    existing: dict[str, dict[str, object]] = {}
    if manifest_path.exists():
        existing_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing = {
            str(item["job_key"]): item
            for item in existing_payload.get("results", [])
            if item.get("status") == "COMPLETED"
        }
    planned = [
        {
            "job_key": _job_key(category, variant, model, number),
            "category": category,
            "variant": variant,
            "model": model,
            "number": number,
        }
        for category, variant, model, number in jobs
    ]
    pending = [job for job in jobs if _job_key(*job) not in existing]
    if args.dry_run:
        print(json.dumps({"planned": len(jobs), "pending": len(pending), "workers": args.workers}, ensure_ascii=False, indent=2))
        return 0

    results = dict(existing)
    print(json.dumps({"status": "STARTED", "planned": len(jobs), "pending": len(pending), "workers": args.workers}, ensure_ascii=False))
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                _run_job,
                output=args.output,
                category=category,
                variant=variant,
                model=model,
                number=number,
                max_completion_tokens=args.max_completion_tokens,
                repair_attempts=args.repair_attempts,
                prompt_path=args.prompt_path,
            ): _job_key(category, variant, model, number)
            for category, variant, model, number in pending
        }
        for future in as_completed(futures):
            result = future.result()
            results[str(result["job_key"])] = result
            print(json.dumps(result, ensure_ascii=False))
            manifest_path.write_text(
                json.dumps(
                    {
                        "schema_version": "generation_matrix_manifest_v1",
                        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "planned_count": len(jobs),
                        "completed_count": sum(
                            item.get("status") == "COMPLETED" for item in results.values()
                        ),
                        "planned": planned,
                        "results": sorted(results.values(), key=lambda item: str(item["job_key"])),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
    failed = sum(item.get("status") == "FAILED" for item in results.values())
    print(json.dumps({"status": "COMPLETED", "planned": len(jobs), "results": len(results), "failed": failed, "manifest": str(manifest_path)}, ensure_ascii=False, indent=2))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
