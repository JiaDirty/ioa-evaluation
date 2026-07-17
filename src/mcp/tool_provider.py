"""Expose configured MCP tools through the ToolGateway provider interface."""

from __future__ import annotations

from typing import Any

from src.tools.models import ToolDescriptor, ToolResult
from src.tools.registry import ToolRegistry

from .client import MCPClient
from .server_registry import MCPServerRegistry


class MCPToolProvider:
    provider_name = "mcp"

    def __init__(self, server_registry: MCPServerRegistry, client: MCPClient | None = None) -> None:
        self.server_registry = server_registry
        self.client = client or MCPClient()

    async def sync_tools(self, tool_registry: ToolRegistry, server_id: str | None = None) -> int:
        if server_id is not None:
            servers = [self.server_registry.get(server_id)]
        else:
            servers = self.server_registry.list_enabled_servers()

        synced = 0
        for server in servers:
            if not server.enabled:
                continue
            tools = await self.client.list_tools(server)
            for tool in tools:
                if not tool.tool_name:
                    continue
                if not server.allows_tool(tool.tool_name):
                    continue
                descriptor = ToolDescriptor(
                    tool_id=self.tool_id(server.server_id, tool.tool_name),
                    name=tool.tool_name,
                    description=tool.description,
                    input_schema=tool.input_schema,
                    output_schema=tool.output_schema,
                    risk_level=server.risk_level,
                    provider="mcp",
                    endpoint=server.endpoint or "",
                    timeout_seconds=server.timeout_seconds,
                    metadata={
                        "provider": "mcp",
                        "server_id": server.server_id,
                        "tool_name": tool.tool_name,
                        **tool.metadata,
                    },
                )
                tool_registry.register(descriptor)
                synced += 1
        return synced

    async def call(self, descriptor: ToolDescriptor, arguments: dict[str, Any]) -> ToolResult:
        server_id = str(descriptor.metadata.get("server_id", ""))
        tool_name = str(descriptor.metadata.get("tool_name", descriptor.name))
        if not server_id:
            return ToolResult(
                call_id="",
                tool_id=descriptor.tool_id,
                status="failed",
                error="MCP descriptor missing server_id",
            )
        try:
            server = self.server_registry.get(server_id)
        except KeyError as exc:
            return ToolResult(
                call_id="",
                tool_id=descriptor.tool_id,
                status="failed",
                error=str(exc),
            )
        if not server.enabled:
            return ToolResult(
                call_id="",
                tool_id=descriptor.tool_id,
                status="denied",
                error=f"MCP server disabled: {server_id}",
            )
        if not server.allows_tool(tool_name):
            return ToolResult(
                call_id="",
                tool_id=descriptor.tool_id,
                status="denied",
                error=f"MCP tool not allowlisted: {tool_name}",
            )
        result = await self.client.call_tool(server, tool_name, arguments)
        return ToolResult(
            call_id="",
            tool_id=descriptor.tool_id,
            status=result.status if result.status in {"completed", "failed", "denied"} else "failed",
            output=result.output,
            error=result.error,
            metadata={
                "provider": "mcp",
                "server_id": server_id,
                "tool_name": tool_name,
                **result.metadata,
            },
        )

    @staticmethod
    def tool_id(server_id: str, tool_name: str) -> str:
        return f"mcp:{server_id}:{tool_name}"
