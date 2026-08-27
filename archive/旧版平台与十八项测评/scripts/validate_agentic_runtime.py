from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.data_models import Task, TaskStatus, TaskType
from src.decision_agents import TaskSpecificationAgent
from src.experiment.runner import IoAEnvironment
from src.orchestration.planner import AgenticOrchestrationPlanner


async def main() -> None:
    prompt = "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较旅行保险；任何购买必须先确认。"
    env = IoAEnvironment({
        "offline_deterministic": True,
        "execution_mode": "offline_deterministic",
        "create_agent_runtimes": False,
        "enable_live_decision_agents": False,
        "enable_live_judges": False,
        "auto_bind_deterministic_runtimes": True,
    })
    for sid in ["finance", "healthcare", "travel", "news"]:
        env.add_sub_ioa(sid)
    await env.setup_default_agents()
    await env.setup_default_topology("full_mesh")

    task = Task(task_type=TaskType.DYNAMIC, prompt=prompt, description=prompt, execution_mode="offline_deterministic")
    spec = TaskSpecificationAgent().specify(prompt=prompt, constraints=task.constraints)
    serialized_spec = json.dumps(spec.model_dump(mode="json"), ensure_ascii=False).lower()
    for token in ["agent_id", "endpoint", "hop_chain", "target_sub_ioas"]:
        assert token not in serialized_spec, token

    graph = AgenticOrchestrationPlanner().build_graph(task, spec)
    for node in graph.nodes:
        assert node.assigned_agent_id is None
        assert node.target_id is None
        serialized_node_metadata = json.dumps(node.metadata, ensure_ascii=False).lower()
        for token in ["endpoint", "hop_chain", "target_sub_ioas"]:
            assert token not in serialized_node_metadata, token

    result = await env.submit_task(task)
    assert result.status == TaskStatus.COMPLETED, result.error
    assert result.participating_agents, "no runtime-bound agents participated"
    entry_events = [
        event for event in env.event_bus.query(task_id=result.task_id)
        if event.event_type == "entry_gateway_selected"
    ]
    assert entry_events, "entry gateway selection was not recorded"
    assert entry_events[0].payload["entry_sub_ioa_id"] != "finance", "entry gateway looks lexicographic for travel prompt"
    print("validate_agentic_runtime: OK")


if __name__ == "__main__":
    asyncio.run(main())
