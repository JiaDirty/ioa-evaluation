"""A no-network client used only for protocol and fixture validation."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


class ScriptedBusinessClient:
    def __init__(self, turns: list[dict[str, Any]]) -> None:
        self.turns = [deepcopy(turn) for turn in turns]
        self.calls: list[dict[str, Any]] = []

    def generate_chat_turn(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append({"messages": deepcopy(messages), **deepcopy(kwargs)})
        if not self.turns:
            raise RuntimeError("scripted client has no remaining turn")
        turn = self.turns.pop(0)
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
        }, ensure_ascii=False)
        return {
            "content": content,
            "tool_calls": [],
            "finish_reason": "stop",
            "assistant_message": {"role": "assistant", "content": content},
        }
