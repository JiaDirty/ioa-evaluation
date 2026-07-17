"""Minimal HTTP JSON-RPC MCP client adapter."""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import requests

from .models import MCPServerConfig, MCPToolCallResult, MCPToolInfo


class MCPClient:
    async def list_tools(self, server: MCPServerConfig) -> list[MCPToolInfo]:
        if server.transport != "http":
            raise NotImplementedError("MCP stdio transport is not implemented")
        payload = self._jsonrpc_payload("tools/list", {})
        response = await self._post(server, payload)
        result = response.get("result", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [self._parse_tool(server.server_id, item) for item in tools if isinstance(item, dict)]

    async def call_tool(
        self,
        server: MCPServerConfig,
        tool_name: str,
        arguments: dict[str, Any],
    ) -> MCPToolCallResult:
        if server.transport != "http":
            return MCPToolCallResult(
                server_id=server.server_id,
                tool_name=tool_name,
                status="failed",
                error="MCP stdio transport is not implemented",
            )
        payload = self._jsonrpc_payload(
            "tools/call",
            {"name": tool_name, "arguments": arguments},
        )
        try:
            response = await self._post(server, payload)
        except Exception as exc:
            return MCPToolCallResult(
                server_id=server.server_id,
                tool_name=tool_name,
                status="failed",
                error=str(exc),
            )
        if response.get("error"):
            return MCPToolCallResult(
                server_id=server.server_id,
                tool_name=tool_name,
                status="failed",
                error=str(response["error"]),
            )
        result = response.get("result", {})
        output = result if isinstance(result, dict) else {"content": result}
        return MCPToolCallResult(
            server_id=server.server_id,
            tool_name=tool_name,
            status="completed",
            output=output,
        )

    @staticmethod
    def _jsonrpc_payload(method: str, params: dict[str, Any]) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": method,
            "params": params,
        }

    async def _post(self, server: MCPServerConfig, payload: dict[str, Any]) -> dict[str, Any]:
        if not server.endpoint:
            raise ValueError(f"MCP server {server.server_id} has no endpoint")

        def send() -> dict[str, Any]:
            headers = {"Content-Type": "application/json"}
            token = server.auth.get("token") if isinstance(server.auth, dict) else None
            if token:
                headers["Authorization"] = f"Bearer {token}"
            response = requests.post(
                server.endpoint,
                json=payload,
                headers=headers,
                timeout=server.timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict):
                raise ValueError("MCP HTTP response must be a JSON object")
            return data

        return await asyncio.to_thread(send)

    @staticmethod
    def _parse_tool(server_id: str, item: dict[str, Any]) -> MCPToolInfo:
        name = str(item.get("name") or item.get("tool_name") or "")
        return MCPToolInfo(
            server_id=server_id,
            tool_name=name,
            description=str(item.get("description", "")),
            input_schema=dict(item.get("inputSchema") or item.get("input_schema") or {}),
            output_schema=dict(item.get("outputSchema") or item.get("output_schema") or {}),
            metadata={k: v for k, v in item.items() if k not in {"name", "tool_name", "description", "inputSchema", "input_schema", "outputSchema", "output_schema"}},
        )
