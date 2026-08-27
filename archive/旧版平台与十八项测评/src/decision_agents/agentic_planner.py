"""Agentic planning decision component.

The component emits a capability-level graph. It never binds concrete Agent IDs,
endpoints, certificates, or scripted hop chains.
"""

from __future__ import annotations

from ..core.data_models import Task, TaskSpec
from ..orchestration.graph import ExecutionGraph
from ..orchestration.planner import AgenticOrchestrationPlanner


class AgenticPlannerAgent:
    name = "AgenticPlannerAgent"

    def __init__(self, planner: AgenticOrchestrationPlanner | None = None) -> None:
        self.planner = planner or AgenticOrchestrationPlanner()

    def plan(self, task: Task, task_spec: TaskSpec) -> ExecutionGraph:
        return self.planner.build_graph(task, task_spec)
