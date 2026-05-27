"""实验相关 API 路由。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..schemas import ExperimentRunRequest, ExperimentRunResponse

router = APIRouter(prefix="/api/experiments", tags=["experiments"])

_active_experiments: dict[str, dict] = {}

SEEDS_DIR = Path(__file__).parent.parent.parent / "data" / "seeds"


@router.get("/reports")
async def list_reports() -> list[dict]:
    """列出已有报告。"""
    results_dir = Path(__file__).parent.parent.parent / "results"
    if not results_dir.exists():
        return []
    reports = []
    for f in sorted(results_dir.glob("experiment_report_*.json"), reverse=True):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            reports.append({
                "id": f.stem,
                "timestamp": data.get("timestamp", ""),
                "total_tests": data.get("summary", {}).get("total_tests", 0),
                "passed": data.get("summary", {}).get("passed", 0),
                "failed": data.get("summary", {}).get("failed", 0),
            })
        except Exception:
            continue
    return reports


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict:
    """获取单个报告详情。"""
    results_dir = Path(__file__).parent.parent.parent / "results"
    filepath = results_dir / f"{report_id}.json"
    if not filepath.exists():
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
    """后台执行实验。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from src.experiment.runner import ExperimentRunner, IoAEnvironment
    from risk_tests.registry import ALL_TESTS, TESTS_BY_CATEGORY, get_test

    try:
        env = IoAEnvironment()
        for sub_ioa_id in ["finance", "healthcare", "travel", "news"]:
            env.add_sub_ioa(sub_ioa_id)
        await env.setup_default_agents()
        await env.setup_default_topology(req.topology)

        runner = ExperimentRunner(env)

        if req.mode == "all":
            tests = ALL_TESTS
        elif req.mode == "category" and req.category:
            tests = TESTS_BY_CATEGORY.get(req.category, [])
        elif req.mode == "single" and req.test_id:
            t = get_test(req.test_id)
            tests = [t] if t else []
        else:
            tests = ALL_TESTS

        _active_experiments[exp_id]["total"] = len(tests)

        for i, test in enumerate(tests):
            _active_experiments[exp_id]["current"] = i + 1
            _active_experiments[exp_id]["current_test"] = test.test_id

            result = await runner.run_single_test(test.test_id, test.run)

            if "results" not in _active_experiments[exp_id]:
                _active_experiments[exp_id]["results"] = []
            _active_experiments[exp_id]["results"].append({
                "test_id": result.test_id,
                "passed": result.passed,
                "risk_level": result.risk_level.value,
            })

            await asyncio.sleep(0.1)

        report = await runner.generate_report()
        _active_experiments[exp_id]["status"] = "completed"
        _active_experiments[exp_id]["report"] = report

        results_dir = Path(__file__).parent.parent.parent / "results"
        results_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = results_dir / f"experiment_report_{timestamp}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        _active_experiments[exp_id]["status"] = "failed"
        _active_experiments[exp_id]["error"] = str(e)


async def _run_scenario_task(exp_id: str, scenario_file: str):
    """后台执行场景实验。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from src.experiment.scenario_loader import ScenarioLoader
    from src.experiment.runner import ExperimentRunner, IoAEnvironment

    try:
        scenario_path = SEEDS_DIR / scenario_file
        if not scenario_path.exists():
            raise FileNotFoundError(f"Scenario file not found: {scenario_file}")

        loader = ScenarioLoader(scenario_path)
        scenario = loader.load()

        _active_experiments[exp_id]["total"] = 1
        _active_experiments[exp_id]["current_test"] = scenario.scenario_name

        env = IoAEnvironment()
        await env.setup_from_scenario(scenario)

        runner = ExperimentRunner(env)

        # 执行场景任务
        task = env.build_task_from_scenario(scenario)
        task_result = await runner.run_task_scenario(task)

        _active_experiments[exp_id]["current"] = 1
        _active_experiments[exp_id]["results"] = [{
            "test_id": scenario.scenario_id,
            "passed": task_result.status.value == "completed",
            "risk_level": scenario.risk.risk_level,
        }]

        report = await runner.generate_report()
        report["scenario"] = {
            "scenario_id": scenario.scenario_id,
            "scenario_name": scenario.scenario_name,
            "risk_dimension": scenario.risk.dimension,
            "attack_type": scenario.attack.attack_type,
        }

        _active_experiments[exp_id]["status"] = "completed"
        _active_experiments[exp_id]["report"] = report

        results_dir = Path(__file__).parent.parent.parent / "results"
        results_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = results_dir / f"scenario_report_{timestamp}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

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
