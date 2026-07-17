"""Structured models for configured MCP servers and tools."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class MCPServerConfig(BaseModel):
    server_id: str
    name: str
    enabled: bool = False
    transport: Literal["http", "stdio"] = "http"
    endpoint: str | None = None
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    auth: dict[str, Any] = Field(default_factory=dict)
    allowed_tools: list[str] = Field(default_factory=list)
    timeout_seconds: int = 30
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    sandbox: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def allows_tool(self, tool_name: str) -> bool:
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools


class MCPToolInfo(BaseModel):
    server_id: str
    tool_name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MCPToolCallRequest(BaseModel):
    server_id: str
    tool_name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class MCPToolCallResult(BaseModel):
    server_id: str
    tool_name: str
    status: str
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
