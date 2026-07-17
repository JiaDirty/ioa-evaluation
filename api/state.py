"""Shared API runtime state."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from src.experiment.runner import IoAEnvironment

_env: IoAEnvironment | None = None
_envs: dict[str, IoAEnvironment] = {}
_lock = asyncio.Lock()
task_store: dict[str, dict] = {}
DEFAULT_SQLITE_PATH = Path("data/ioa_runtime.sqlite3")


async def get_ioa_env(execution_mode: str | None = None) -> IoAEnvironment:
    global _env
    mode = "agentic_live" if execution_mode == "agentic_live" else "offline_deterministic"
    if mode in _envs:
        return _envs[mode]
    if mode == "offline_deterministic" and _env is not None:
        _envs[mode] = _env
        return _env
    async with _lock:
        if mode in _envs:
            return _envs[mode]
        live = mode == "agentic_live"
        env = IoAEnvironment({
            "offline_deterministic": not live,
            "execution_mode": mode,
            "create_agent_runtimes": live,
            "enable_live_attack_injector": live,
            "enable_live_decision_agents": live,
            "enable_live_judges": live,
            "enable_safety_judge": live,
            "auto_bind_deterministic_runtimes": not live,
        })
        for sub_ioa_id in ["finance", "healthcare", "travel", "news"]:
            env.add_sub_ioa(sub_ioa_id)
        await env.setup_default_agents()
        await env.setup_default_topology("full_mesh")
        _envs[mode] = env
        if not live:
            _env = env
        return env


def reset_state(clear_persistence: bool = True) -> None:
    global _env
    _env = None
    _envs.clear()
    task_store.clear()
    if clear_persistence and DEFAULT_SQLITE_PATH.exists():
        with sqlite3.connect(DEFAULT_SQLITE_PATH) as conn:
            for table in [
                "tasks", "events", "tool_calls", "artifacts", "execution_spans",
                "observation_payloads", "observability_sequence",
            ]:
                try:
                    conn.execute(f"DELETE FROM {table}")
                except sqlite3.OperationalError:
                    continue
