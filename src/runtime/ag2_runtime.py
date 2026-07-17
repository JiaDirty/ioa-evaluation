"""Adapter for existing IoA/AG2-style agent runtimes."""

from __future__ import annotations

import inspect
import json
from typing import Any

from pydantic import TypeAdapter

from .actions import AgentAction, FinalAction
from .base import AgentInvocation, AgentInvocationResult, AgentRuntime


_ACTION_ADAPTER = TypeAdapter(AgentAction)


class AG2AgentRuntime(AgentRuntime):
    runtime_type = "ag2"

    def __init__(
        self,
        agent_id: str,
        card: Any,
        ioa_agent: Any,
        default_max_turns: int = 1,
    ) -> None:
        self.agent_id = agent_id
        self.card = card
        self.ioa_agent = ioa_agent
        self.default_max_turns = default_max_turns

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        prompt = self._build_prompt(invocation)
        max_turns = int(invocation.metadata.get("max_turns", self.default_max_turns))
        try:
            result = self.ioa_agent.run_task(prompt, max_turns=max_turns)
            if inspect.isawaitable(result):
                result = await result
            action = self._parse_action(result)
            if action is None and invocation.metadata.get("agentic_loop"):
                action = FinalAction(
                    answer=result,
                    limitations=["AG2 runtime returned text; wrapped as FinalAction by the controlled adapter."],
                    confidence=0.6,
                )
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                status="completed",
                output={"text": result} if action is None or action.type != "final" else {"text": action.answer},
                action=action,
                metadata={
                    "runtime_type": self.runtime_type,
                    "max_turns": max_turns,
                    "tool_gateway_available": "tool_context" in invocation.metadata,
                    "agentic_loop": bool(invocation.metadata.get("agentic_loop")),
                },
            )
        except Exception as exc:
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                status="failed",
                error=str(exc),
                metadata={
                    "runtime_type": self.runtime_type,
                    "max_turns": max_turns,
                },
            )

    def get_card(self) -> dict[str, Any]:
        if hasattr(self.card, "model_dump"):
            return self.card.model_dump(mode="json")
        if isinstance(self.card, dict):
            return self.card
        return {"agent_id": self.agent_id}

    async def call_tool_via_gateway(
        self,
        invocation: AgentInvocation,
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        tool_context = invocation.metadata.get("tool_context")
        if tool_context is None:
            raise ValueError("ToolGateway context is not available for AG2 runtime")
        result = await tool_context.call_tool(tool_id, arguments)
        return result.model_dump(mode="json")

    @staticmethod
    def _build_prompt(invocation: AgentInvocation) -> str:
        task = invocation.input.get("task") or invocation.input.get("description") or ""
        payload = invocation.input.get("payload", {})
        return (
            f"任务ID：{invocation.task_id}\n"
            f"Trace ID：{invocation.trace_id}\n"
            f"请求方：{invocation.requester_id}\n\n"
            f"任务：\n{task}\n\n"
            f"输入负载：\n{payload}\n\n"
            f"上下文：\n{invocation.context}\n\n"
            f"权限：\n{invocation.permissions}\n"
            "工具治理：所有工具调用必须通过 ToolGateway，不要直接调用 legacy tool。\n"
            "Agentic 模式只允许返回结构化 AgentAction；不要声称已执行未授权动作。\n"
        )

    @staticmethod
    def _parse_action(value: Any) -> AgentAction | None:
        if isinstance(value, dict):
            payload = value
        elif isinstance(value, str) and value.strip().startswith("{"):
            try:
                payload = json.loads(value)
            except json.JSONDecodeError:
                return None
        else:
            return None
        if not isinstance(payload, dict) or "type" not in payload:
            return None
        try:
            return _ACTION_ADAPTER.validate_python(payload)
        except Exception:
            return None
