"""Deterministic-safe replanning helper for unfinished graph nodes."""

from __future__ import annotations

from ..core.data_models import CapabilityRequirement
from ..orchestration.graph import ExecutionGraph, ExecutionNode, StepStatus


class ReplanningAgent:
    name = "ReplanningAgent"

    def __init__(self, model_client=None) -> None:
        self.model_client = model_client

    def add_unfinished_capability(
        self,
        graph: ExecutionGraph,
        *,
        reason: str,
        requirement: CapabilityRequirement,
        depends_on: list[str] | None = None,
    ) -> ExecutionGraph:
        completed_ids = {
            node.node_id
            for node in graph.nodes
            if node.status == StepStatus.COMPLETED
        }
        for node in graph.nodes:
            if node.node_id in completed_ids:
                continue
            node.metadata.setdefault("replan_context", []).append(reason)
        new_node = ExecutionNode(
            node_id=f"replan-{len(graph.nodes) + 1}",
            node_type="agent_task",
            label=f"Replanned capability: {requirement.capability}",
            depends_on=depends_on or list(completed_ids),
            subtask_description=requirement.semantic_description,
            required_capabilities=[requirement],
            expected_output=requirement.expected_output,
            metadata={"replanned": True, "reason": reason},
        )
        graph.nodes.append(new_node)
        graph.refresh_edges()
        return graph
