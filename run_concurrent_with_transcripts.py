"""Run all 18 IOA risk tests concurrently with per-agent Q&A transcripts.

Each test gets an isolated IoAEnvironment to avoid registry, audit, topology,
and shared-knowledge contamination across concurrent tests.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

sys.path.insert(0, str(Path(__file__).parent))

from risk_tests.registry import ALL_TESTS, TESTS_BY_ID
from src.core.data_models import EvaluationStatus
from src.experiment.runner import ExperimentRunner, IoAEnvironment

SUB_IOAS = ["finance", "healthcare", "travel", "news"]
PRESET_FULL_ENV_TESTS = {"ioa_behavior_inference"}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("ioa-concurrent")


def _safe_dump(obj: Any) -> Any:
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return str(obj)


def install_agent_transcript_logger(
    env: IoAEnvironment,
    test_id: str,
    transcript_path: Path,
) -> Callable[[], list[dict[str, Any]]]:
    """Wrap env.run_agent_task and persist every domain-agent prompt/response."""
    original = env.run_agent_task
    lock = threading.Lock()
    records: list[dict[str, Any]] = []
    counter = {"value": 0}

    def write_record(record: dict[str, Any]) -> None:
        with lock:
            records.append(record)
            with transcript_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def logged_run_agent_task(
        sub_ioa_id: str,
        agent_id_or_task: str,
        task: str | None = None,
        max_turns: int = 1,
    ) -> str:
        with lock:
            counter["value"] += 1
            call_index = counter["value"]

        if task is None:
            agent_id = sub_ioa_id
            prompt = agent_id_or_task
            invocation = "direct_sub_ioa_agent"
        else:
            agent_id = agent_id_or_task
            prompt = task
            invocation = "endpoint_agent_card"

        started = datetime.now()
        t0 = time.time()
        status = "completed"
        response = ""
        error = ""
        try:
            response = original(sub_ioa_id, agent_id_or_task, task, max_turns=max_turns)
            return response
        except Exception as exc:
            status = "failed"
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            write_record({
                "test_id": test_id,
                "call_index": call_index,
                "started_at": started.isoformat(),
                "ended_at": datetime.now().isoformat(),
                "duration_seconds": round(time.time() - t0, 4),
                "status": status,
                "sub_ioa_id": sub_ioa_id,
                "agent_id": agent_id,
                "invocation": invocation,
                "max_turns": max_turns,
                "prompt": prompt,
                "response": response,
                "error": error,
            })

    env.run_agent_task = logged_run_agent_task  # type: ignore[method-assign]
    return lambda: records


async def setup_isolated_environment(test_id: str, transcript_path: Path) -> tuple[IoAEnvironment, Callable[[], list[dict[str, Any]]]]:
    env = IoAEnvironment()
    get_records = install_agent_transcript_logger(env, test_id, transcript_path)
    # Most risk tests create only the Sub-IoAs they need. Pre-building every
    # domain for every concurrent test creates hundreds of unnecessary live
    # LLM runtimes. Keep the environment minimal unless a test assumes a fully
    # populated topology without doing its own setup.
    if test_id in PRESET_FULL_ENV_TESTS:
        for sub_ioa_id in SUB_IOAS:
            env.add_sub_ioa(sub_ioa_id)
        await env.setup_default_agents()
        await env.setup_default_topology("full_mesh")
    return env, get_records


async def run_one_test(test, output_dir: Path, semaphore: asyncio.Semaphore) -> dict[str, Any]:
    async with semaphore:
        test_dir = output_dir / test.test_id
        test_dir.mkdir(parents=True, exist_ok=True)
        transcript_path = test_dir / "agent_transcripts.jsonl"
        transcript_path.write_text("", encoding="utf-8")

        env: IoAEnvironment | None = None
        start = time.time()
        try:
            env, get_records = await setup_isolated_environment(test.test_id, transcript_path)
            runner = ExperimentRunner(env)
            result = await runner.run_single_test(test.test_id, test.run)
            report = await runner.generate_report()
            records = get_records()
            status = "valid" if result.status == EvaluationStatus.VALID else "invalid"
            payload = {
                "test_id": test.test_id,
                "test_name": test.test_name,
                "category": test.category,
                "status": status,
                "passed": result.passed,
                "risk_level": result.risk_level.value,
                "confidence": result.confidence,
                "explanation": result.explanation,
                "execution_time": result.execution_time,
                "agent_transcript_count": len(records),
                "transcript_path": str(transcript_path),
                "result": result.model_dump(),
            }
            (test_dir / "test_result.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            (test_dir / "isolated_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            logger.info("[%s] %s transcripts=%d", test.test_id, "PASS" if result.passed else "FAIL", len(records))
            return payload
        except Exception as exc:
            payload = {
                "test_id": test.test_id,
                "test_name": test.test_name,
                "category": test.category,
                "status": "runner_error",
                "passed": False,
                "risk_level": "CRITICAL",
                "confidence": 0.0,
                "explanation": f"Concurrent runner error: {type(exc).__name__}: {exc}",
                "execution_time": time.time() - start,
                "agent_transcript_count": 0,
                "transcript_path": str(transcript_path),
                "traceback": traceback.format_exc(),
            }
            (test_dir / "runner_error.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            logger.exception("[%s] failed in concurrent runner", test.test_id)
            return payload
        finally:
            if env is not None and getattr(env, "_local_endpoint_server", None) is not None:
                try:
                    env._local_endpoint_server.stop()
                except Exception:
                    logger.warning("[%s] failed to stop local endpoint server", test.test_id)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="", help="Output directory. Defaults to results/concurrent_transcripts_<timestamp>.")
    parser.add_argument("--concurrency", type=int, default=18, help="Number of tests to run concurrently.")
    parser.add_argument("--worker-test-id", default="", help=argparse.SUPPRESS)
    args = parser.parse_args()

    root = Path(__file__).parent
    output_dir = (
        Path(args.output)
        if args.output
        else root / "results" / f"concurrent_transcripts_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.worker_test_id:
        test = TESTS_BY_ID[args.worker_test_id]
        result = await run_one_test(test, output_dir, asyncio.Semaphore(1))
        worker_path = output_dir / test.test_id / "worker_result.json"
        worker_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return

    await run_concurrent_processes(output_dir, max(1, args.concurrency))


async def run_concurrent_processes(output_dir: Path, concurrency: int) -> None:
    root = Path(__file__).parent
    started = time.time()
    semaphore = asyncio.Semaphore(concurrency)

    async def launch_worker(test) -> dict[str, Any]:
        async with semaphore:
            test_dir = output_dir / test.test_id
            test_dir.mkdir(parents=True, exist_ok=True)
            log_path = test_dir / "worker_stdout.log"
            cmd = [
                sys.executable,
                str(Path(__file__).resolve()),
                "--worker-test-id",
                test.test_id,
                "--output",
                str(output_dir),
            ]
            logger.info("Launching worker: %s", test.test_id)
            with log_path.open("w", encoding="utf-8") as log:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(root),
                    stdout=log,
                    stderr=asyncio.subprocess.STDOUT,
                )
                code = await proc.wait()
            logger.info("Worker finished: %s code=%s", test.test_id, code)
            return {"test_id": test.test_id, "returncode": code, "log_path": str(log_path)}

    worker_results = await asyncio.gather(*(launch_worker(test) for test in ALL_TESTS))
    collected: list[dict[str, Any]] = []
    for test in ALL_TESTS:
        test_dir = output_dir / test.test_id
        result_path = test_dir / "test_result.json"
        error_path = test_dir / "runner_error.json"
        if result_path.exists():
            collected.append(json.loads(result_path.read_text(encoding="utf-8")))
        elif error_path.exists():
            collected.append(json.loads(error_path.read_text(encoding="utf-8")))
        else:
            worker = next((item for item in worker_results if item["test_id"] == test.test_id), {})
            collected.append({
                "test_id": test.test_id,
                "test_name": test.test_name,
                "category": test.category,
                "status": "missing_result",
                "passed": False,
                "risk_level": "CRITICAL",
                "confidence": 0.0,
                "explanation": f"Worker did not produce result file; returncode={worker.get('returncode')}",
                "execution_time": 0.0,
                "agent_transcript_count": 0,
                "transcript_path": str(test_dir / "agent_transcripts.jsonl"),
            })

    valid = [r for r in collected if r["status"] == "valid"]
    summary = {
        "timestamp": datetime.now().isoformat(),
        "execution_mode": "concurrent_process_isolated_live_llm",
        "output_dir": str(output_dir),
        "concurrency": concurrency,
        "duration_seconds": round(time.time() - started, 4),
        "total_tests": len(collected),
        "valid_tests": len(valid),
        "invalid_or_runner_error": len(collected) - len(valid),
        "passed": sum(1 for r in valid if r["passed"]),
        "failed": sum(1 for r in valid if not r["passed"]),
        "valid_pass_rate": (
            sum(1 for r in valid if r["passed"]) / len(valid)
            if valid else 0.0
        ),
        "agent_transcript_count": sum(int(r.get("agent_transcript_count", 0)) for r in collected),
        "workers": worker_results,
        "tests": collected,
    }
    write_summary_files(output_dir, summary)
    logger.info("Summary written to %s", output_dir / "RUN_SUMMARY.md")


def write_summary_files(output_dir: Path, summary: dict[str, Any]) -> None:
    (output_dir / "concurrent_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )

    results = summary["tests"]
    lines = [
        "# 并发 18 项 IoA 风险测试运行摘要",
        "",
        f"- 输出目录：{output_dir}",
        f"- 执行模式：{summary['execution_mode']}",
        f"- 并发度：{summary['concurrency']}",
        f"- 总测试数：{summary['total_tests']}",
        f"- 有效测试数：{summary['valid_tests']}",
        f"- 无效或运行器错误：{summary['invalid_or_runner_error']}",
        f"- 通过：{summary['passed']}",
        f"- 失败：{summary['failed']}",
        f"- 有效通过率：{summary['valid_pass_rate']:.4f}",
        f"- Agent 问答记录总数：{summary['agent_transcript_count']}",
        f"- 总耗时秒：{summary['duration_seconds']}",
        "",
        "## 测试明细",
        "",
    ]
    for item in results:
        lines.append(
            f"- {item['test_id']}：{'通过' if item['passed'] else '失败'}，"
            f"状态 {item['status']}，问答 {item.get('agent_transcript_count', 0)} 条，"
            f"记录 {item.get('transcript_path', '')}"
        )
    (output_dir / "RUN_SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
