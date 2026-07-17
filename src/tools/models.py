"""Structured tool call models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ToolDescriptor(BaseModel):
    tool_id: str
    name: str
    description: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    required_scopes: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high", "critical"] = "low"
    provider: Literal["local", "mcp", "http"] = "local"
    endpoint: str = ""
    timeout_seconds: int = 30
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolCall(BaseModel):
    call_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    tool_id: str
    task_id: str = ""
    trace_id: str = ""
    parent_span_id: str | None = None
    caller_agent_id: str = ""
    arguments: dict[str, Any] = Field(default_factory=dict)
    granted_scopes: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)


class ToolResult(BaseModel):
    call_id: str
    tool_id: str
    status: Literal["completed", "failed", "denied"] = "completed"
    output: Any = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)
