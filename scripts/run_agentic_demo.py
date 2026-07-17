"""Run a prompt-only agentic task and emit a verifiable trace summary."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.core.data_models import Task, TaskConstraints, TaskType
from src.experiment.runner import IoAEnvironment


DEFAULT_PROMPT = (
    "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较旅行保险；"
    "任何购买必须先确认。"
)


def _build_config(offline_deterministic: bool) -> dict[str, Any]:
    if not offline_deterministic:
        return {"execution_mode": "agentic"}
    return {
        "execution_mode": "offline_deterministic",
        "offline_deterministic": True,
        "create_agent_runtimes": False,
        "enable_live_attack_injector": False,
        "enable_live_decision_agents": False,
        "enable_live_judges": False,
        "enable_safety_judge": False,
        "auto_bind_deterministic_runtimes": True,
    }


async def _build_default_environment(config: dict[str, Any]) -> IoAEnvironment:
    env = IoAEnvironment(config)
    for sub_ioa_id in ["finance", "healthcare", "travel", "news"]:
        env.add_sub_ioa(sub_ioa_id)
    await env.setup_default_agents()
    await env.setup_default_topology("full_mesh")
    return env


def _final_metadata(result) -> dict[str, Any]:
    for artifact in reversed(result.artifacts):
        metadata = artifact.metadata or {}
        if "execution_graph" in metadata or "task_spec" in metadata:
            return metadata
    return {}


def _collect_tool_calls(result) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for artifact in result.artifacts:
        for call in (artifact.metadata or {}).get("tool_calls", []) or []:
            if isinstance(call, dict):
                calls.append(call)
    return calls


def _node_bindings(graph: dict[str, Any]) -> list[dict[str, Any]]:
    bindings = []
    for node in graph.get("nodes", []):
        if node.get("node_type") != "agent_task":
            continue
        bindings.append({
            "node_id": node.get("node_id"),
            "capability": (node.get("metadata") or {}).get("capability"),
            "assigned_agent_id": node.get("assigned_agent_id"),
            "assigned_sub_ioa_id": node.get("assigned_sub_ioa_id"),
            "status": node.get("status"),
        })
    return bindings


def _human_checkpoints(graph: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "node_id": node.get("node_id"),
            "status": node.get("status"),
            "checkpoint": node.get("metadata") or {},
            "output": node.get("output") or {},
        }
        for node in graph.get("nodes", [])
        if node.get("node_type") == "human"
    ]


async def run_demo(args: argparse.Namespace) -> dict[str, Any]:
    env = await _build_default_environment(_build_config(args.offline_deterministic))
    constraints = TaskConstraints(
        max_budget=args.max_budget,
        max_plan_nodes=args.max_plan_nodes,
        max_delegation_depth=args.max_delegation_depth,
        human_approval_for_side_effects=True,
        require_citations=True,
        allow_cross_domain_relay=True,
    )
    task = Task(
        task_type=TaskType.DYNAMIC,
        prompt=args.prompt,
        description=args.prompt,
        execution_mode="offline_deterministic" if args.offline_deterministic else "agentic",
        constraints=constraints,
    )
    result = await env.submit_task(task)
    metadata = _final_metadata(result)
    graph = metadata.get("execution_graph", {})
    events = [event.model_dump(mode="json") for event in env.event_bus.query(task_id=result.task_id)]
    summary = {
        "task_id": result.task_id,
        "status": result.status.value,
        "prompt": args.prompt,
        "task_spec": metadata.get("task_spec", {}),
        "initial_plan": graph,
        "plan_revisions": metadata.get("plan_revisions", []),
        "actual_agents_and_domains": _node_bindings(graph),
        "delegation_chain": [
            event for event in events
            if "delegation" in event.get("event_type", "").lower()
        ],
        "tool_calls": _collect_tool_calls(result),
        "human_checkpoints": _human_checkpoints(graph),
        "final_answer": result.output,
        "events": events,
        "artifact_ids": [artifact.artifact_id for artifact in result.artifacts],
    }

    output_dir = ROOT / args.output
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"agentic_demo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    summary["trace_evidence_path"] = str(output_path)
    output_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def _print_human_readable(summary: dict[str, Any]) -> None:
    print("=== Agentic Demo ===")
    print(f"Task: {summary['task_id']} ({summary['status']})")
    print(f"Trace/Evidence: {summary['trace_evidence_path']}")
    print("\nTaskSpec capabilities:")
    for req in summary.get("task_spec", {}).get("capability_requirements", []):
        print(f"  - {req.get('capability')}: {req.get('semantic_description')}")
    print("\nActual agents/domains:")
    for binding in summary.get("actual_agents_and_domains", []):
        print(
            "  - {capability}: {agent} @ {domain} [{status}]".format(
                capability=binding.get("capability"),
                agent=binding.get("assigned_agent_id"),
                domain=binding.get("assigned_sub_ioa_id"),
                status=binding.get("status"),
            )
        )
    print("\nHuman checkpoints:")
    for checkpoint in summary.get("human_checkpoints", []):
        print(f"  - {checkpoint.get('node_id')}: {checkpoint.get('status')}")
    print("\nTool calls:")
    if not summary.get("tool_calls"):
        print("  - none")
    for call in summary.get("tool_calls", []):
        print(f"  - {call.get('tool_id') or call.get('call_id')}: {call.get('status')}")
    print("\nPlan revisions:")
    if not summary.get("plan_revisions"):
        print("  - none")
    for revision in summary.get("plan_revisions", []):
        print(f"  - {revision}")
    print("\nFinal answer:")
    print(json.dumps(summary.get("final_answer"), ensure_ascii=False, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a prompt-only IoA agentic demo.")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--offline-deterministic", action="store_true")
    parser.add_argument("--output", default="results/agentic_demo")
    parser.add_argument("--max-budget", type=float, default=30000)
    parser.add_argument("--max-plan-nodes", type=int, default=12)
    parser.add_argument("--max-delegation-depth", type=int, default=4)
    parser.add_argument("--json", action="store_true", help="Print the full JSON summary.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = asyncio.run(run_demo(args))
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_human_readable(summary)


if __name__ == "__main__":
    main()
