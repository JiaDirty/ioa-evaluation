"""A no-network client used only for protocol and fixture validation."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


class ScriptedBusinessClient:
    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = [deepcopy(turn) for turn in turns]
        self.calls: list[dict[str, Any]] = []
        self.last_provider_calls: list[dict[str, Any]] = []
        self.last_usage: dict[str, Any] | None = None
        self.last_retry_count: int = 0
        self.last_latency_ms: float | None = None
        self.last_response_metadata: dict[str, Any] = {}
        self.last_request_budget: dict[str, Any] = {}

    def generate_chat_turn(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": deepcopy(messages), **deepcopy(kwargs)})
        if not self.turns:
            raise RuntimeError("scripted client has no remaining turn")
        turn = self.turns.pop(0)
        self.last_provider_calls = deepcopy(turn.get("provider_calls", []))
        self.last_usage = deepcopy(turn.get("usage"))
        self.last_retry_count = int(turn.get("retry_count", 0) or 0)
        self.last_latency_ms = turn.get("latency_ms")
        self.last_response_metadata = deepcopy(turn.get("response_metadata", {}))
        self.last_request_budget = deepcopy(turn.get("request_budget", {}))
        tool_calls = turn.get("tool_calls", [])
        content = turn.get("content")
        assistant_message = turn.get("assistant_message") or {
            "role": "assistant",
            "content": content,
            **({"tool_calls": tool_calls} if tool_calls else {}),
        }
        return {
            "content": content,
            "tool_calls": tool_calls,
            "finish_reason": "tool_calls" if tool_calls else "stop",
            "assistant_message": assistant_message,
            **({
                "visible_reasoning": turn["visible_reasoning"],
                "visible_reasoning_field": turn.get(
                    "visible_reasoning_field", "message.reasoning_content"
                ),
            } if "visible_reasoning" in turn else {}),
        }


class ProtocolValidationClient:
    """Return schema-valid text without pretending to model business behavior."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate_chat_turn(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": deepcopy(messages), **deepcopy(kwargs)})
        content = json.dumps({
            "status": "COMPLETED",
            "decision": "完成离线请求协议检查。",
            "answer": "该结果只证明输入、输出和运行链可解析，不代表被测模型的业务行为。",
            "evidence_refs": [],
            "next_action": "无",
            "handoff_message": "离线协议检查完成；没有产生真实模型业务结论。",
            "decision_basis": "仅验证统一输入、工具消息和最终 JSON 的协议闭环。",
        }, ensure_ascii=False)
        return {
            "content": content,
            "tool_calls": [],
            "finish_reason": "stop",
            "assistant_message": {"role": "assistant", "content": content},
        }
