"""Observable orchestration execution graph models."""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from ..core.data_models import ArtifactBinding, CapabilityRequirement


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class ExecutionNode(BaseModel):
    node_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    node_type: Literal[
        "verify",
        "policy_check",
        "agent_task",
        "tool",
        "delegation",
        "human",
        "synthesis",
        "agent",
        "aggregate",
    ] = "agent_task"
    label: str
    status: StepStatus = StepStatus.PENDING
    target_id: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    subtask_description: str = ""
    required_capabilities: list[CapabilityRequirement] = Field(default_factory=list)
    input_bindings: list[ArtifactBinding] = Field(default_factory=list)
    expected_output: str = ""
    output_schema: dict[str, Any] = Field(default_factory=dict)
    assigned_agent_id: str | None = None
    assigned_sub_ioa_id: str | None = None
    max_attempts: int = 2
    replan_on_failure: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionEdge(BaseModel):
    source: str
    target: str
    edge_type: str = "depends_on"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ExecutionGraph(BaseModel):
    graph_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    task_id: str
    trace_id: str
    failure_strategy: Literal["fail_fast", "continue"] = "fail_fast"
    nodes: list[ExecutionNode] = Field(default_factory=list)
    edges: list[ExecutionEdge] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def node_by_id(self, node_id: str) -> ExecutionNode | None:
        return next((node for node in self.nodes if node.node_id == node_id), None)

    def refresh_edges(self) -> None:
        self.edges = [
            ExecutionEdge(source=dependency, target=node.node_id)
            for node in self.nodes
            for dependency in node.depends_on
        ]
