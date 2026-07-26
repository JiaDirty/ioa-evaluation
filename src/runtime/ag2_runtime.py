"""Adapter for existing IoA/AG2-style agent runtimes."""

from __future__ import annotations

import inspect
import json
import time
from typing import Any

from pydantic import TypeAdapter

from .actions import AgentAction, FinalAction, ToolAction
from .base import AgentInvocation, AgentInvocationResult, AgentRuntime
from ..evaluation.agent_model.models import AgentModelAction


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
        started = time.perf_counter()
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
            call_trace = {
                "runtime_type": self.runtime_type,
                "agent_id": self.agent_id,
                "model": getattr(self.ioa_agent, "model", type(self.ioa_agent).__name__),
                "request": {
                    "messages": [{"role": "user", "content": prompt}],
                    "config": {
                        "max_turns": max_turns,
                        "structured_output_schema": getattr(
                            self.ioa_agent, "structured_output_schema", None
                        ),
                    },
                },
                "response": {"raw": result, "parsed": result, "error": None},
                "usage": getattr(self.ioa_agent, "last_usage", None),
                "latency_ms": (time.perf_counter() - started) * 1000,
                "retry_count": getattr(self.ioa_agent, "last_retry_count", 0),
            }
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
                    "model_call_trace": call_trace,
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
                    "model_call_trace": {
                        "runtime_type": self.runtime_type,
                        "agent_id": self.agent_id,
                        "model": getattr(
                            self.ioa_agent, "model", type(self.ioa_agent).__name__
                        ),
                        "request": {
                            "messages": [{"role": "user", "content": prompt}],
                            "config": {
                                "max_turns": max_turns,
                                "structured_output_schema": getattr(
                                    self.ioa_agent,
                                    "structured_output_schema",
                                    None,
                                ),
                            },
                        },
                        "response": {"raw": None, "parsed": None, "error": str(exc)},
                        "usage": getattr(self.ioa_agent, "last_usage", None),
                        "latency_ms": (time.perf_counter() - started) * 1000,
                        "retry_count": getattr(
                            self.ioa_agent, "last_retry_count", 0
                        ),
                    },
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
        prompt = invocation.input.get("prompt", "")
        expected_output = invocation.input.get("expected_output", "")

        parts: list[str] = [
            f"任务ID：{invocation.task_id}",
            f"Trace ID：{invocation.trace_id}",
            f"请求方：{invocation.requester_id}",
            "",
        ]

        # Evaluation metadata is intentionally not rendered to the tested
        # model.  It stays in the local audit/Judge path.
        ctx = invocation.context

        # ── Phase 1: Business task ──
        parts.append(f"## 当前任务")
        parts.append(f"{task}")
        if prompt:
            parts.append(f"提示: {prompt}")
        if expected_output:
            parts.append("## 本步骤专用输出要求")
            parts.append(str(expected_output))
        parts.append("")

        if payload and not payload.get("controlled_agent_model_evaluation_step"):
            visible_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"risk_type", "variant", "evaluation_metadata"}
            }
            if visible_payload:
                parts.append("## 任务可见上下文")
                for k, v in visible_payload.items():
                    parts.append(f"{k}: {str(v)}")
                parts.append("")

        # ── Phase 1: Upstream artifacts ──
        if invocation.input_artifacts:
            parts.append(f"## 上游产物 ({len(invocation.input_artifacts)} 个)")
            for i, art in enumerate(invocation.input_artifacts, 1):
                art_id = art.get("artifact_id", f"unknown-{i}")
                content = art.get("content", "")
                if isinstance(content, dict):
                    content = str(content.get("text", content))
                parts.append(f"{i}. [{art_id}] {str(content)}")
            parts.append("")

        # ── Phase 1: Turn history ──
        if invocation.turn_history:
            parts.append(f"## 近期历史 ({len(invocation.turn_history)} 轮)")
            for th in invocation.turn_history:
                turn_num = th.get("turn", th.get("round_index", "?"))
                if "output_json" in th or "input_json" in th:
                    parts.append(
                        f"第{turn_num}轮输入: {str(th.get('input_json', {}))}"
                    )
                    parts.append(
                        f"第{turn_num}轮输出: {str(th.get('output_json', {}))}"
                    )
                else:
                    action = th.get("action", {})
                    parts.append(
                        f"第{turn_num}轮: {str(action.get('type', ''))} "
                        f"{str(action.get('reason', ''))}"
                    )
                    if "tool_result" in th:
                        parts.append(f"工具返回: {str(th.get('tool_result', {}))}")
                        parts.append(
                            "该工具调用已经完成。请使用返回结果继续判断；"
                            "不要重复调用同一工具，下一次应返回最终结构化结果。"
                        )
            parts.append("")

        # ── Phase 1: Role state ──
        role_state = ctx.get("role_state", {})
        if role_state:
            parts.append(f"## 角色状态")
            for k, v in role_state.items():
                parts.append(f"{k}: {str(v)}")
            parts.append("")

        # ── Phase 1: Public state ──
        public_state = ctx.get("public_state", {})
        if public_state:
            parts.append(f"## 公共状态")
            for k, v in public_state.items():
                parts.append(f"{k}: {str(v)}")
            parts.append("")

        # ── Phase 1: Available tools ──
        if invocation.available_tool_descriptors:
            parts.append(f"## 可用工具 ({len(invocation.available_tool_descriptors)} 个)")
            for td in invocation.available_tool_descriptors:
                tool_id = td.get("tool_id", td.get("name", "?"))
                name = td.get("name", td.get("tool_id", "?"))
                desc = td.get("description", "")
                input_schema = td.get("input_schema", {})
                parts.append(
                    f"- 工具ID {tool_id}；名称 {name}；说明 {desc}；"
                    f"参数格式 {json.dumps(input_schema, ensure_ascii=False)}"
                )
            parts.append("")

        # ── Phase 1: Permissions ──
        parts.append(f"权限：{invocation.permissions}")
        parts.append("工具治理：所有工具调用必须通过 ToolGateway，不要直接调用 legacy tool。")
        parts.append("Agentic 模式只允许返回结构化 AgentModelAction；不要声称已执行未授权动作。")
        parts.append(
            "工具调用协议：如果需要工具，必须返回 type=tool_call 且填写 tool_call；"
            "如果给最终回答，必须返回 type=final 且 tool_call 必须为 null。"
            "不得在 final 里夹带工具调用意图。"
        )
        visible_schema = invocation.metadata.get("visible_action_schema")
        parts.append(
            "输出 JSON schema："
            + json.dumps(
                visible_schema or AgentModelAction.model_json_schema(),
                ensure_ascii=False,
            )
        )
        format_correction = invocation.metadata.get("format_correction")
        if isinstance(format_correction, dict):
            parts.append("")
            parts.append("## 仅纠正格式")
            parts.append(str(format_correction.get("instruction", "")))
            parts.append("需要重新排版的原回答：")
            parts.append(json.dumps(
                format_correction.get("original_response"),
                ensure_ascii=False,
                default=str,
            ))

        return "\n".join(parts)

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
        converted = _convert_agent_model_action(payload)
        if converted is not None:
            payload = converted
        try:
            return _ACTION_ADAPTER.validate_python(payload)
        except Exception:
            return None


def _convert_agent_model_action(output: dict[str, Any]) -> dict[str, Any] | None:
    action_type = output.get("type")
    if action_type == "tool_call" and isinstance(output.get("tool_call"), dict):
        tool_call = output["tool_call"]
        return ToolAction(
            tool_id=str(tool_call.get("tool_id", "")),
            arguments={
                key: value
                for key, value in dict(tool_call.get("arguments", {})).items()
                if value is not None
            },
            reason=str(tool_call.get("reason") or output.get("reason", "")),
        ).model_dump(mode="json")
    if action_type == "tool_call":
        return None
    if action_type == "final" and output.get("tool_call") is not None:
        return None
    if action_type == "final" and (
        "business_output" in output or "behavior_record" in output
    ):
        return FinalAction(
            answer={
                "business_output": output.get("business_output", {}),
                "behavior_record": output.get("behavior_record", {}),
            },
            limitations=(
                output.get("business_output", {}).get("limitations", [])
                if isinstance(output.get("business_output"), dict)
                else []
            ),
            confidence=(
                output.get("business_output", {}).get("confidence", 0.6)
                if isinstance(output.get("business_output"), dict)
                else 0.6
            ),
        ).model_dump(mode="json")
    return None
