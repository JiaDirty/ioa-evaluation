"""反馈循环 API 路由。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _load_latest_report() -> dict | None:
    results_dir = Path(__file__).parent.parent.parent / "results"
    if not results_dir.exists():
        return None
    files = sorted(results_dir.glob("experiment_report_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/summary")
async def get_feedback_summary() -> dict:
    """获取反馈循环摘要。"""
    report = _load_latest_report()
    if not report:
        return {"error": "No report found"}
    return report.get("feedback_loop", {})


@router.get("/actions")
async def get_feedback_actions() -> list[dict]:
    """获取反馈动作列表。"""
    report = _load_latest_report()
    if not report:
        return []
    return report.get("feedback_actions", [])
