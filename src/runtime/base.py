"""Unified Agent runtime contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .actions import AgentAction


class AgentInvocation(BaseModel):
    task_id: str
    trace_id: str
    requester_id: str = "user"
    agent_id: str
    input: dict[str, Any] = Field(default_factory=dict)
    subtask: dict[str, Any] = Field(default_factory=dict)
    task_spec_summary: dict[str, Any] = Field(default_factory=dict)
    plan_summary: dict[str, Any] = Field(default_factory=dict)
    available_tool_descriptors: list[dict[str, Any]] = Field(default_factory=list)
    input_artifacts: list[dict[str, Any]] = Field(default_factory=list)
    delegation_grant: dict[str, Any] = Field(default_factory=dict)
    turn_history: list[dict[str, Any]] = Field(default_factory=list)
    remaining_budget: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class AgentInvocationResult(BaseModel):
    task_id: str
    trace_id: str
    agent_id: str
    status: Literal["completed", "failed", "cancelled", "input_required"] = "completed"
    output: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    agent_calls: list[dict[str, Any]] = Field(default_factory=list)
    action: AgentAction | None = None
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class AgentRuntime(ABC):
    runtime_type: str = "base"

    @abstractmethod
    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        raise NotImplementedError

    @abstractmethod
    def get_card(self) -> dict[str, Any]:
        raise NotImplementedError
