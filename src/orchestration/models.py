"""Orchestration plan models."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from .graph import ExecutionGraph, ExecutionNode


class OrchestrationStep(BaseModel):
    step_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    agent_id: str
    sub_ioa_id: str = ""
    role: str = "selected_agent"
    depends_on: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)


class OrchestrationPlan(BaseModel):
    plan_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    task_id: str
    mode: Literal["single", "parallel"] = "single"
    steps: list[OrchestrationStep] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_execution_graph(self, trace_id: str | None = None) -> ExecutionGraph:
        verify_node = ExecutionNode(
            node_id=f"verify-{self.plan_id}",
            node_type="verify",
            label="Task intake and policy verification",
            metadata={"plan_id": self.plan_id},
        )
        nodes: list[ExecutionNode] = [verify_node]
        for step in self.steps:
            dependencies = step.depends_on or [verify_node.node_id]
            nodes.append(
                ExecutionNode(
                    node_id=step.step_id,
                    node_type="agent",
                    label=f"{step.role}: {step.agent_id}",
                    target_id=step.agent_id,
                    depends_on=dependencies,
                    input=step.input,
                    metadata={
                        "sub_ioa_id": step.sub_ioa_id,
                        "role": step.role,
                    },
                )
            )
        aggregate_dependencies = [node.node_id for node in nodes]
        nodes.append(
            ExecutionNode(
                node_id=f"aggregate-{self.plan_id}",
                node_type="aggregate",
                label="Aggregate agent outputs",
                depends_on=aggregate_dependencies,
                metadata={"plan_id": self.plan_id, "mode": self.mode},
            )
        )
        graph = ExecutionGraph(
            graph_id=f"graph-{self.plan_id}",
            task_id=self.task_id,
            trace_id=trace_id or self.task_id,
            nodes=nodes,
            metadata={"plan_id": self.plan_id, "mode": self.mode, **self.metadata},
        )
        graph.refresh_edges()
        return graph
