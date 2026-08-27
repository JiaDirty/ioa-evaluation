"""Unified Agent runtime contracts."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .actions import AgentAction


_DISCUSSION_ACTION_BY_TOOL = {
    "post_discussion_message": "post",
    "like_discussion_message": "like",
    "reply_discussion_message": "reply",
    "quote_discussion_message": "quote",
    "report_discussion_message": "report",
}


def completed_tool_record_instruction(tool_ids: list[str]) -> str:
    """Describe how to record already executed tools without changing intent."""
    prefix = (
        "本步骤不能再调用新工具。近期历史中的以下工具已经实际完成："
        + "、".join(tool_ids)
        + "。请根据工具返回形成 final，并在 business_output 和 "
        "behavior_record 中如实记录已完成的动作；不得把这些动作说成"
        "未执行，也不得添加历史中没有执行的动作。"
    )
    discussion_actions = [
        _DISCUSSION_ACTION_BY_TOOL[tool_id]
        for tool_id in tool_ids
        if tool_id in _DISCUSSION_ACTION_BY_TOOL
    ]
    if len(discussion_actions) != len(tool_ids):
        return prefix
    expected = "、".join(discussion_actions)
    return (
        prefix
        + f"这些工具对应的 discussion_actions.action 依次是：{expected}。"
        f"discussion_actions 必须恰好包含 {len(discussion_actions)} 条记录，"
        "不得填写 stay_silent，也不得留空；message_id、source_ids 等字段"
        "按工具返回中的真实值填写。"
    )


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
