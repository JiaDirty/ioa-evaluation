"""实验相关 API 路由。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..schemas import ExperimentRunRequest, ExperimentRunResponse

router = APIRouter(prefix="/api/experiments", tags=["experiments"])

_active_experiments: dict[str, dict] = {}

SEEDS_DIR = Path(__file__).parent.parent.parent / "data" / "seeds"
RESULTS_DIR = Path(__file__).parent.parent.parent / "results"


def _iter_report_files() -> list[Path]:
    if not RESULTS_DIR.exists():
        return []
    files = [
        path for path in RESULTS_DIR.rglob("*report_*.json")
        if path.is_file() and "node_modules" not in path.parts
    ]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def _report_id(path: Path) -> str:
    return path.stem


def _find_report_file(report_id: str) -> Path | None:
    safe_id = Path(report_id).name
    for path in _iter_report_files():
        if path.stem == safe_id:
            return path
    return None


def _judge_summary(data: dict[str, Any]) -> dict[str, Any]:
    scenario = data.get("scenario") or {}
    summary = data.get("summary") or {}
    scenario_eval = data.get("scenario_evaluation") or {}
    judge = data.get("judge_verdict") or {}
    outcome = judge.get("outcome") or {}
    injection = judge.get("injection_assessment") or {}
    trigger = judge.get("trigger_assessment") or {}
    vulnerability = judge.get("vulnerability") or {}
    return {
        "scenario_id": scenario.get("scenario_id", ""),
        "scenario_name": scenario.get("scenario_name", ""),
        "attack_type": scenario.get("attack_type", ""),
        "risk_dimension": scenario.get("risk_dimension", ""),
        "judge_status": outcome.get("status") or scenario_eval.get("judge_status") or summary.get("judge_status", ""),
        "maximum_stage": outcome.get("maximum_stage") or scenario_eval.get("maximum_stage", ""),
        "attack_triggered": bool(trigger.get("triggered", scenario_eval.get("attack_triggered", False))),
        "injection_applied": bool(injection.get("applied", False)),
        "vulnerable_components": vulnerability.get("components") or scenario_eval.get("vulnerable_components", []),
        "evidence_ids": scenario_eval.get("evidence_ids", []),
        "status_counts": summary.get("status_counts", {}),
    }


def _result_message_from_report(report: dict[str, Any]) -> dict[str, Any]:
    scenario = report.get("scenario") or {}
    judge = _judge_summary(report)
    return {
        "test_id": scenario.get("scenario_id") or report.get("test_id", ""),
        "passed": bool(report.get("scenario_evaluation", {}).get("passed", False)),
        "risk_level": report.get("scenario", {}).get("risk_level", "MEDIUM"),
        "judge_status": judge["judge_status"],
        "maximum_stage": judge["maximum_stage"],
        "attack_type": judge["attack_type"],
        "attack_triggered": judge["attack_triggered"],
        "injection_applied": judge["injection_applied"],
        "evidence_ids": judge["evidence_ids"],
        "vulnerable_components": judge["vulnerable_components"],
    }


def _aggregate_seed_reports(reports: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts: dict[str, int] = {
        "NOT_TRIGGERED": 0,
        "ATTEMPTED_BLOCKED": 0,
        "PARTIAL_SUCCESS": 0,
        "SUCCESS": 0,
        "SUCCESS_WITH_IMPACT": 0,
        "INDETERMINATE": 0,
    }
    scenario_runs = []
    for report in reports:
        judge = _judge_summary(report)
        status = judge["judge_status"] or "INDETERMINATE"
        if status not in status_counts:
            status_counts[status] = 0
        status_counts[status] += 1
        scenario_runs.append({
            **judge,
            "scenario_id": judge["scenario_id"] or report.get("scenario", {}).get("scenario_id", ""),
            "source_report": report.get("source_report", ""),
        })
    return {
        "timestamp": datetime.now().isoformat(),
        "summary": {
            "total_tests": len(reports),
            "judge_status_counts": status_counts,
            "status_counts": status_counts,
            "execution_mode": reports[0].get("summary", {}).get("execution_mode", "") if reports else "",
        },
        "scenario_runs": scenario_runs,
    }


@router.get("/reports")
async def list_reports() -> list[dict]:
    """列出已有报告。"""
    reports = []
    for f in _iter_report_files():
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            judge = _judge_summary(data)
            reports.append({
                "id": _report_id(f),
                "relative_path": str(f.relative_to(RESULTS_DIR)),
                "timestamp": data.get("timestamp", ""),
                "total_tests": data.get("summary", {}).get("total_tests", 0),
                "passed": data.get("summary", {}).get("passed", 0),
                "failed": data.get("summary", {}).get("failed", 0),
                "scenario_id": judge["scenario_id"],
                "attack_type": judge["attack_type"],
                "judge_status": judge["judge_status"],
                "maximum_stage": judge["maximum_stage"],
                "status_counts": judge["status_counts"],
            })
        except Exception:
            continue
    return reports


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict:
    """获取单个报告详情。"""
    filepath = _find_report_file(report_id)
    if filepath is None or not filepath.exists():
        return {"error": "Report not found"}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/run")
async def run_experiment(req: ExperimentRunRequest) -> ExperimentRunResponse:
    """触发实验运行。"""
    exp_id = f"exp-{uuid.uuid4().hex[:8]}"
    _active_experiments[exp_id] = {"status": "running", "current": 0, "total": 0}

    asyncio.create_task(_run_experiment_task(exp_id, req))

    return ExperimentRunResponse(experiment_id=exp_id, status="started")


@router.get("/scenarios")
async def list_scenarios() -> list[dict]:
    """列出所有可用的 seed 场景文件。"""
    if not SEEDS_DIR.exists():
        return []
    scenarios = []
    for f in sorted(SEEDS_DIR.glob("seed_*.json")):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            scenarios.append({
                "file": f.name,
                "scenario_id": data.get("scenario_id", ""),
                "scenario_name": data.get("scenario_name", ""),
                "risk_dimension": data.get("risk", {}).get("dimension", ""),
                "risk_dimension_cn": data.get("risk", {}).get("dimension_cn", ""),
                "risk_level": data.get("risk", {}).get("risk_level", ""),
                "attack_type": data.get("attack", {}).get("attack_type", ""),
                "difficulty": data.get("metadata", {}).get("difficulty", ""),
            })
        except Exception:
            continue
    return scenarios


@router.post("/run-scenario")
async def run_scenario_experiment(scenario_file: str) -> ExperimentRunResponse:
    """从指定场景文件触发实验运行。"""
    exp_id = f"exp-{uuid.uuid4().hex[:8]}"
    _active_experiments[exp_id] = {"status": "running", "current": 0, "total": 0}

    asyncio.create_task(_run_scenario_task(exp_id, scenario_file))

    return ExperimentRunResponse(experiment_id=exp_id, status="started")


async def _run_experiment_task(exp_id: str, req: ExperimentRunRequest):
    """后台执行 seed/Judge 实验。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from run_experiment import run_scenario, save_report
    from src.experiment.runner import IoAEnvironment
    from src.experiment.scenario_loader import load_all_seeds

    try:
        scenarios = load_all_seeds(str(SEEDS_DIR))
        if req.mode == "category" and req.category:
            scenarios = [s for s in scenarios if s.risk.dimension == req.category]
        elif req.mode == "single" and req.test_id:
            scenarios = [
                s for s in scenarios
                if req.test_id in {s.scenario_id, s.attack.attack_type, Path(s.source_path).name}
            ]
        else:
            scenarios = scenarios

        env_config = {
            "experiment_id": exp_id,
            "execution_mode": req.execution_mode,
            "offline_deterministic": req.execution_mode == "offline_deterministic",
            "create_agent_runtimes": req.execution_mode != "offline_deterministic",
            "enable_live_attack_injector": req.execution_mode == "agentic_live",
            "enable_live_decision_agents": req.execution_mode == "agentic_live",
            "enable_live_judges": req.execution_mode == "agentic_live",
            "enable_safety_judge": req.execution_mode == "agentic_live",
            "auto_bind_deterministic_runtimes": req.execution_mode == "offline_deterministic",
        }
        reports: list[dict[str, Any]] = []
        _active_experiments[exp_id]["total"] = len(scenarios)

        for i, scenario in enumerate(scenarios):
            _active_experiments[exp_id]["current"] = i + 1
            _active_experiments[exp_id]["current_test"] = scenario.scenario_id

            env = IoAEnvironment(env_config)
            report = await run_scenario(env, scenario)
            saved_path = save_report(report, "results/api_seed_runs")
            report["source_report"] = saved_path
            reports.append(report)

            if "results" not in _active_experiments[exp_id]:
                _active_experiments[exp_id]["results"] = []
            _active_experiments[exp_id]["results"].append(_result_message_from_report(report))

            await asyncio.sleep(0.1)

        report = _aggregate_seed_reports(reports)
        _active_experiments[exp_id]["status"] = "completed"
        _active_experiments[exp_id]["report"] = report

        results_dir = RESULTS_DIR / "api_seed_runs"
        results_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = results_dir / f"experiment_report_{timestamp}_aggregate.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        _active_experiments[exp_id]["status"] = "failed"
        _active_experiments[exp_id]["error"] = str(e)


async def _run_scenario_task(exp_id: str, scenario_file: str):
    """后台执行场景实验。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from run_experiment import run_scenario, save_report
    from src.experiment.runner import IoAEnvironment
    from src.experiment.scenario_loader import ScenarioLoader

    try:
        scenario_path = SEEDS_DIR / scenario_file
        if not scenario_path.exists():
            raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

        loader = ScenarioLoader(scenario_path)
        scenario = loader.load()

        _active_experiments[exp_id]["total"] = 1
        _active_experiments[exp_id]["current_test"] = scenario.scenario_name

        env = IoAEnvironment({
            "experiment_id": exp_id,
            "execution_mode": "agentic_live",
            "create_agent_runtimes": True,
            "enable_live_attack_injector": True,
            "enable_live_decision_agents": True,
            "enable_live_judges": True,
            "enable_safety_judge": True,
        })
        report = await run_scenario(env, scenario)
        saved_path = save_report(report, "results/api_seed_runs")
        report["source_report"] = saved_path

        _active_experiments[exp_id]["current"] = 1
        _active_experiments[exp_id]["results"] = [_result_message_from_report(report)]

        _active_experiments[exp_id]["status"] = "completed"
        _active_experiments[exp_id]["report"] = report

    except Exception as e:
        _active_experiments[exp_id]["status"] = "failed"
        _active_experiments[exp_id]["error"] = str(e)


@router.websocket("/ws/{exp_id}/progress")
async def ws_progress(websocket: WebSocket, exp_id: str):
    """WebSocket 实时进度推送。"""
    await websocket.accept()

    if exp_id not in _active_experiments:
        await websocket.send_json({"type": "error", "message": "Experiment not found"})
        await websocket.close()
        return

    last_result_count = 0
    try:
        while True:
            exp = _active_experiments.get(exp_id, {})
            status = exp.get("status", "unknown")

            results = exp.get("results", [])
            if len(results) > last_result_count:
                for r in results[last_result_count:]:
                    await websocket.send_json({"type": "result", **r})
                last_result_count = len(results)

            await websocket.send_json({
                "type": "progress",
                "current": exp.get("current", 0),
                "total": exp.get("total", 0),
                "test_id": exp.get("current_test", ""),
                "status": status,
            })

            if status == "completed":
                await websocket.send_json({"type": "complete", "report": exp.get("report", {})})
                break
            elif status == "failed":
                await websocket.send_json({"type": "error", "message": exp.get("error", "Unknown error")})
                break

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass


@router.websocket("/{exp_id}/stream")
async def ws_observability_stream(websocket: WebSocket, exp_id: str):
    """Stream experiment progress and nested task events with replay support."""
    await websocket.accept()
    if exp_id not in _active_experiments:
        await websocket.send_json({"type": "error", "message": "Experiment not found"})
        await websocket.close()
        return
    after_sequence = int(websocket.query_params.get("after_sequence", "0") or 0)
    last_result_count = 0
    try:
        from api.state import get_ioa_env
        env = await get_ioa_env()
        while True:
            exp = _active_experiments.get(exp_id, {})
            for event in env.event_bus.query(experiment_id=exp_id, after_sequence=after_sequence):
                after_sequence = max(after_sequence, event.sequence)
                await websocket.send_json({"type": "event", "event": event.model_dump(mode="json")})
            results = exp.get("results", [])
            for result in results[last_result_count:]:
                await websocket.send_json({"type": "result", **result})
            last_result_count = len(results)
            await websocket.send_json({
                "type": "progress",
                "current": exp.get("current", 0),
                "total": exp.get("total", 0),
                "test_id": exp.get("current_test", ""),
                "status": exp.get("status", "unknown"),
                "last_sequence": after_sequence,
            })
            if exp.get("status") == "completed":
                await websocket.send_json({"type": "complete", "report": exp.get("report", {})})
                break
            if exp.get("status") == "failed":
                await websocket.send_json({"type": "error", "message": exp.get("error", "Unknown error")})
                break
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return
