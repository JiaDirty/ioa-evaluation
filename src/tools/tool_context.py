"""Runtime-facing helper for gateway-governed tool calls."""

from __future__ import annotations

from typing import Any

from .gateway import ToolGateway
from .models import ToolCall, ToolResult


class ToolExecutionContext:
    def __init__(
        self,
        gateway: ToolGateway,
        task_id: str,
        trace_id: str,
        agent_id: str,
        granted_scopes: list[str] | None = None,
    ) -> None:
        self.gateway = gateway
        self.task_id = task_id
        self.trace_id = trace_id
        self.agent_id = agent_id
        self.granted_scopes = granted_scopes or []

    async def call_tool(self, tool_id: str, arguments: dict[str, Any]) -> ToolResult:
        call = ToolCall(
            task_id=self.task_id,
            trace_id=self.trace_id,
            caller_agent_id=self.agent_id,
            tool_id=tool_id,
            arguments=arguments,
            granted_scopes=self.granted_scopes,
        )
        return await self.gateway.call_tool(call)
