"""Orchestration planners for scripted and agentic execution modes."""

from __future__ import annotations

from typing import Any

from ..core.data_models import Task, TaskSpec
from .graph import ExecutionGraph, ExecutionNode
from .models import OrchestrationPlan, OrchestrationStep


class LegacyScriptedOrchestrationPlanner:
    def build_plan(self, task: Task, candidates: list[Any]) -> OrchestrationPlan:
        enabled = bool(task.payload.get("enable_multi_agent_orchestration"))
        max_agents = int(task.payload.get("max_agents", 1 if not enabled else 2))
        selected = candidates[: max(1, max_agents)]
        mode = "parallel" if enabled and len(selected) > 1 else "single"
        return OrchestrationPlan(
            task_id=task.task_id,
            mode=mode,
            steps=[
                OrchestrationStep(
                    agent_id=agent.agent_id,
                    sub_ioa_id=getattr(agent, "sub_ioa_id", ""),
                    role="primary" if index == 0 else "supporting",
                    input={"description": task.description},
                )
                for index, agent in enumerate(selected)
            ],
            metadata={
                "enabled": enabled,
                "requested_max_agents": max_agents,
                "candidate_count": len(candidates),
            },
        )


class SimpleOrchestrationPlanner(LegacyScriptedOrchestrationPlanner):
    """Backward-compatible name for the legacy scripted planner."""


class AgenticOrchestrationPlanner:
    """Build a capability-level DAG without binding concrete Agent IDs."""

    def __init__(self, model_client: Any | None = None) -> None:
        self.model_client = model_client

    def build_graph(self, task: Task, task_spec: TaskSpec) -> ExecutionGraph:
        verify_node = ExecutionNode(
            node_id="policy-precheck",
            node_type="policy_check",
            label="TaskSpec validation and policy precheck",
            status="pending",
            subtask_description="Validate the prompt-derived TaskSpec before planning.",
            expected_output="policy ticket",
        )
        nodes: list[ExecutionNode] = [verify_node]

        previous_node_id = verify_node.node_id
        for index, requirement in enumerate(task_spec.capability_requirements, start=1):
            node = ExecutionNode(
                node_id=f"capability-{index}-{requirement.requirement_id}",
                node_type="agent_task",
                label=f"Capability task: {requirement.capability}",
                depends_on=[previous_node_id] if index > 1 else [verify_node.node_id],
                subtask_description=requirement.semantic_description,
                required_capabilities=[requirement],
                expected_output=requirement.expected_output,
                output_schema={},
                metadata={
                    "requirement_id": requirement.requirement_id,
                    "capability": requirement.capability,
                    "planner_bound_agent": False,
                },
            )
            nodes.append(node)
            if task_spec.estimated_complexity == "multi_stage":
                previous_node_id = node.node_id

        human_dependencies = [
            node.node_id for node in nodes if node.node_type == "agent_task"
        ] or [verify_node.node_id]
        for checkpoint in task_spec.human_checkpoints:
            nodes.append(
                ExecutionNode(
                    node_id=f"human-{checkpoint.checkpoint_id}",
                    node_type="human",
                    label=f"Human checkpoint: {checkpoint.trigger}",
                    depends_on=human_dependencies,
                    subtask_description=checkpoint.reason,
                    expected_output="explicit user input or preserved decision boundary",
                    metadata=checkpoint.model_dump(mode="json"),
                )
            )

        synthesis_dependencies = [
            node.node_id
            for node in nodes
            if node.node_type in {"agent_task", "human"}
        ] or [verify_node.node_id]
        nodes.append(
            ExecutionNode(
                node_id="synthesis",
                node_type="synthesis",
                label="Synthesize sourced final answer",
                depends_on=synthesis_dependencies,
                subtask_description="Synthesize all safe artifacts into a final answer.",
                expected_output="final answer with evidence map",
            )
        )
        graph = ExecutionGraph(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            failure_strategy="continue",
            nodes=nodes,
            metadata={
                "execution_mode": task.execution_mode,
                "task_spec": task_spec.model_dump(mode="json"),
                "planner": "AgenticOrchestrationPlanner",
            },
        )
        graph.refresh_edges()
        return graph
