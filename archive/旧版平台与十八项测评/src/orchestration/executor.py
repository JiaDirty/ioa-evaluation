"""Sequential executor for deterministic local orchestration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .graph import ExecutionGraph, ExecutionNode, StepStatus
from .models import OrchestrationPlan


class OrchestrationExecutor:
    async def execute(
        self,
        plan: OrchestrationPlan,
        step_runner: Callable[[str], Awaitable[Any]],
    ) -> list[Any]:
        results: list[Any] = []
        for step in plan.steps:
            results.append(await step_runner(step.agent_id))
        return results

    async def execute_graph(
        self,
        graph: ExecutionGraph,
        step_runner: Callable[[ExecutionNode], Awaitable[Any]],
    ) -> ExecutionGraph:
        completed: set[str] = set()
        failed: set[str] = set()

        while True:
            ready = [
                node
                for node in graph.nodes
                if node.status == StepStatus.PENDING
                and all(dependency in completed for dependency in node.depends_on)
            ]
            if not ready:
                break

            async def run_node(node: ExecutionNode) -> None:
                node.status = StepStatus.RUNNING
                try:
                    output = await step_runner(node)
                    node.output = output if isinstance(output, dict) else {"result": output}
                    node.status = StepStatus.COMPLETED
                    completed.add(node.node_id)
                except Exception as exc:
                    node.status = StepStatus.FAILED
                    node.error = str(exc)
                    failed.add(node.node_id)

            await __import__("asyncio").gather(*(run_node(node) for node in ready))
            if failed and graph.failure_strategy == "fail_fast":
                for node in graph.nodes:
                    if node.status == StepStatus.PENDING:
                        node.status = StepStatus.SKIPPED
                break

        for node in graph.nodes:
            if node.status == StepStatus.PENDING and any(dep in failed for dep in node.depends_on):
                node.status = StepStatus.SKIPPED
        return graph
