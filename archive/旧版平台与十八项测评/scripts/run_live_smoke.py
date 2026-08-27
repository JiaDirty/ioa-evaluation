from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.data_models import Task, TaskType
from src.experiment.runner import IoAEnvironment
from src.llm.config import get_agent_llm_config, get_judge_llm_config


def _has_llm_key() -> bool:
    if any(
        os.getenv(name)
        for name in ["OPENAI_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY"]
    ):
        return True
    try:
        agent = get_agent_llm_config()
        judge = get_judge_llm_config()
        agent.get_api_key()
        judge.get_api_key()
        return True
    except Exception:
        return False


async def main() -> None:
    if not _has_llm_key():
        print("LIVE_NOT_EXECUTED: no LLM API key found in environment")
        return

    env = IoAEnvironment({
        "execution_mode": "agentic_live",
        "enable_live_decision_agents": True,
        "enable_live_judges": True,
        "enable_live_attack_injector": False,
        "simulate_human_checkpoints": False,
    })
    for sid in ["finance", "healthcare", "travel", "news"]:
        env.add_sub_ioa(sid)
    await env.setup_default_agents()
    await env.setup_default_topology("full_mesh")
    prompt = "为去肯尼亚出差的高管规划行程，评估健康风险并比较保险；不要执行购买。"
    task = Task(task_type=TaskType.DYNAMIC, prompt=prompt, description=prompt, execution_mode="agentic_live")
    result = await env.submit_task(task)
    print(f"LIVE_SMOKE_STATUS: {result.status.value}")
    print(f"LIVE_SMOKE_TASK_ID: {result.task_id}")
    if result.error:
        print(f"LIVE_SMOKE_ERROR: {result.error}")


if __name__ == "__main__":
    asyncio.run(main())
