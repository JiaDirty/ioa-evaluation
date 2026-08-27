"""Runtime registry and compatibility helpers."""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any

from .base import AgentInvocation, AgentInvocationResult, AgentRuntime
from ..tools.tool_context import ToolExecutionContext


class AgentRuntimeManager:
    def __init__(self, tool_gateway: Any | None = None, event_bus: Any | None = None) -> None:
        self._runtimes: dict[str, AgentRuntime] = {}
        self._agent_sub_ioa_index: dict[str, str] = {}
        self.tool_gateway = tool_gateway
        self.event_bus = event_bus

    def set_tool_gateway(self, tool_gateway: Any) -> None:
        self.tool_gateway = tool_gateway

    def bind_runtime(
        self, agent_id: str, runtime: AgentRuntime, sub_ioa_id: str | None = None
    ) -> None:
        self._runtimes[agent_id] = runtime
        if hasattr(runtime, "set_event_bus"):
            runtime.set_event_bus(self.event_bus)
        if sub_ioa_id is not None:
            self._agent_sub_ioa_index[agent_id] = sub_ioa_id

    def has_runtime(self, agent_id: str) -> bool:
        return agent_id in self._runtimes

    def get_runtime(self, agent_id: str) -> AgentRuntime:
        if agent_id not in self._runtimes:
            raise ValueError(f"No runtime bound for AgentCard: {agent_id}")
        return self._runtimes[agent_id]

    def get_agent_sub_ioa(self, agent_id: str) -> str | None:
        return self._agent_sub_ioa_index.get(agent_id)

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        runtime = self.get_runtime(invocation.agent_id)
        if self.tool_gateway is not None and "tool_context" not in invocation.metadata:
            invocation = invocation.model_copy(
                update={
                    "metadata": {
                        **invocation.metadata,
                        "tool_context": ToolExecutionContext(
                            gateway=self.tool_gateway,
                            task_id=invocation.task_id,
                            trace_id=invocation.trace_id,
                            agent_id=invocation.agent_id,
                            granted_scopes=invocation.permissions,
                        ),
                    }
                }
            )
        span = None
        started = time.perf_counter()
        if self.event_bus is not None:
            metadata = {
                key: value for key, value in invocation.metadata.items()
                if key != "tool_context"
            }
            span = self.event_bus.start_span(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                stage="agent_runtime",
                event_type="agent_runtime_started",
                actor_type="agent_runtime",
                actor_id=invocation.agent_id,
                message=f"Agent runtime started for {invocation.agent_id}",
                parent_span_id=metadata.get("parent_span_id"),
                node_id=str(invocation.plan_summary.get("node_id", "")),
                operation="agent_runtime.invoke",
                input={
                    "requester_id": invocation.requester_id,
                    "input": invocation.input,
                    "subtask": invocation.subtask,
                    "task_spec": invocation.task_spec_summary,
                    "plan": invocation.plan_summary,
                    "available_tools": invocation.available_tool_descriptors,
                    "input_artifacts": invocation.input_artifacts,
                    "delegation_grant": invocation.delegation_grant,
                    "turn_history": invocation.turn_history,
                    "context": invocation.context,
                    "permissions": invocation.permissions,
                    "metadata": metadata,
                },
                upstream_ids=[invocation.requester_id],
                downstream_ids=[invocation.agent_id],
            )
        result = await runtime.invoke(invocation)
        if self.event_bus is not None and span is not None:
            duration = (time.perf_counter() - started) * 1000
            failed = result.status == "failed"
            self.event_bus.finish_span(
                span_id=span.span_id,
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                stage="agent_runtime",
                event_type="agent_runtime_failed" if failed else "agent_runtime_completed",
                actor_type="agent_runtime",
                actor_id=invocation.agent_id,
                message=result.error or f"Agent runtime completed for {invocation.agent_id}",
                node_id=str(invocation.plan_summary.get("node_id", "")),
                operation="agent_runtime.invoke",
                phase="failed" if failed else "completed",
                status=result.status,
                duration_ms=duration,
                output=result.model_dump(mode="json"),
                upstream_ids=[invocation.requester_id],
                downstream_ids=[invocation.agent_id],
                error=result.error,
            )
        return result

    def invoke_sync_text(
        self,
        sub_ioa_id: str,
        agent_id: str,
        task_prompt: str,
        max_turns: int = 1,
    ) -> str:
        registered_sub_ioa = self.get_agent_sub_ioa(agent_id)
        if registered_sub_ioa is not None and registered_sub_ioa != sub_ioa_id:
            raise ValueError(f"Agent {agent_id} belongs to {registered_sub_ioa}, not {sub_ioa_id}")

        invocation = AgentInvocation(
            task_id="legacy-sync-task",
            trace_id="legacy-sync-trace",
            requester_id="legacy",
            agent_id=agent_id,
            input={"task": task_prompt},
            context={"sub_ioa_id": sub_ioa_id, "legacy_sync": True},
            metadata={"max_turns": max_turns},
        )
        result = self._run_sync(self.invoke(invocation))
        if result.status != "completed":
            raise ValueError(result.error or f"Runtime failed for {agent_id}")
        return str(result.output.get("text", ""))

    @staticmethod
    def _run_sync(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()

        result_box: dict[str, object] = {}
        error_box: dict[str, BaseException] = {}

        def runner() -> None:
            try:
                result_box["result"] = asyncio.run(coro)
            except BaseException as exc:  # pragma: no cover - defensive bridge
                error_box["error"] = exc

        thread = threading.Thread(target=runner, name="ioa-runtime-sync-bridge")
        thread.start()
        thread.join()
        if "error" in error_box:
            raise error_box["error"]
        return result_box["result"]
