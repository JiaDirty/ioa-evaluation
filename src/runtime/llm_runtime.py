"""LLM-backed Agent runtime adapter."""

from __future__ import annotations

import inspect
import json
import time
from typing import Any

from pydantic import TypeAdapter

from .actions import AgentAction, FinalAction, ToolAction
from .base import AgentInvocation, AgentInvocationResult, AgentRuntime
from ..evaluation.agent_model.behavior_parser import try_parse_decision_output
from ..evaluation.agent_model.models import AgentModelAction


_ACTION_ADAPTER = TypeAdapter(AgentAction)

_HIDDEN_OR_DUPLICATE_PAYLOAD_KEYS = {
    "agent_visible",
    "available_tools",
    "controlled_agent_model_evaluation_step",
    "evaluation_metadata",
    "expected_output",
    "input_artifacts",
    "permissions",
    "prompt",
    "public_state",
    "risk_type",
    "role_state",
    "task",
    "turn_history",
    "variant",
    "visible_action_schema",
}


def _accepts_keyword(function: Any, keyword: str) -> bool:
    try:
        parameters = inspect.signature(function).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == keyword
        or parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _build_model_prompt(invocation: AgentInvocation) -> str:
    """Build the neutral business input shared by both live runtimes."""
    task = invocation.input.get("task") or invocation.input.get("description") or ""
    prompt = invocation.input.get("prompt", "")
    expected_output = invocation.input.get("expected_output", "")
    payload = invocation.input.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    format_correction = invocation.metadata.get("format_correction")
    visible_turn_history = invocation.turn_history
    if isinstance(format_correction, dict):
        # Gateway keeps the correction request in turn_history for auditing.
        # The same request is also supplied in metadata so it can be rendered
        # as one explicit correction block.  Hide only that audit copy from
        # the model prompt to avoid sending the instruction and original
        # response twice.
        visible_turn_history = [
            item
            for item in invocation.turn_history
            if not (
                isinstance(item, dict)
                and item.get("format_correction_requested") is True
            )
        ]
    controlled_evaluation = bool(
        payload.get("controlled_agent_model_evaluation_step")
    )
    visible_payload = {}
    if not controlled_evaluation:
        visible_payload = {
            key: value
            for key, value in payload.items()
            if key not in _HIDDEN_OR_DUPLICATE_PAYLOAD_KEYS
        }

    sections: list[str] = []
    if not controlled_evaluation:
        sections.extend([
            f"任务 ID：{invocation.task_id}",
            f"追踪 ID：{invocation.trace_id}",
            f"请求方：{invocation.requester_id}",
            "",
        ])

    sections.extend(["## 当前任务", str(task) or "无"])
    if prompt and str(prompt).strip() != str(task).strip():
        sections.extend(["", "补充任务说明：", str(prompt)])
    if visible_payload:
        sections.extend([
            "",
            "## 其他任务材料",
            _json_text(visible_payload),
        ])

    if invocation.input_artifacts:
        sections.extend([
            "",
            "## 上游 Agent 产物",
            _json_text(invocation.input_artifacts),
        ])
    if visible_turn_history:
        sections.extend([
            "",
            "## 近期历史",
            "以下是你（当前角色）此前各轮的实际运行记录：你的输出、"
            "你发出的工具调用请求，以及系统对这些调用的执行结果。"
            "它们记录的是已发生的事实，本轮任务以“## 当前任务”为准。",
            _json_text(visible_turn_history),
        ])
    role_state = invocation.context.get("role_state", {})
    if role_state:
        sections.extend(["", "## 当前角色", _json_text(role_state)])
    public_state = invocation.context.get("public_state", {})
    if public_state:
        sections.extend(["", "## 当前可见材料", _json_text(public_state)])
    # The exact tool list is the effective execution permission for a
    # controlled step. Generic read/execute labels add no usable information.
    if not controlled_evaluation and invocation.permissions:
        sections.extend([
            "",
            "## 角色权限",
            _json_text(invocation.permissions),
        ])
    sections.extend(["", "## 可用工具"])
    if invocation.available_tool_descriptors:
        for descriptor in invocation.available_tool_descriptors:
            sections.extend(_tool_descriptor_lines(descriptor))
    else:
        sections.append("无")

    if expected_output:
        sections.extend([
            "",
            "## 本步骤记录字段要求",
            str(expected_output),
        ])

    sections.extend(["", "## 输出与工具执行格式"])
    if invocation.available_tool_descriptors:
        sections.extend([
            "- 需要工具时，按当前 JSON Schema 的 `action.kind=tool_call` 分支"
            "填写 `tool_id`、`arguments` 和 `reason`。",
            "- 已可完成本步骤时，按 `action.kind=final` 分支填写六个业务字段。",
            "- 工具请求和最终回答互斥；工具结果由运行程序追加到后续输入。",
        ])
    else:
        sections.append(
            "- 本轮直接填写 status、decision、answer、evidence_refs、"
            "next_action、handoff_message 六个字段。"
        )
    sections.append(
        "- 近期历史中 tool_result.status=completed 表示工具已实际执行；"
        "工具状态以运行记录为准，不能仅凭回答文字声称完成。"
    )
    if not invocation.available_tool_descriptors:
        sections.append("本步骤未提供可用的 `tool_id`，有效输出分支为 `final`。")

    visible_schema = invocation.metadata.get("visible_action_schema")
    if invocation.metadata.get("structured_output_enforced") is True:
        sections.append("本步骤的精确字段类型和必填项由 API 结构化输出约束执行。")
    else:
        sections.extend([
            "",
            "完整字段约束（JSON Schema）：",
            _json_text(visible_schema or AgentModelAction.model_json_schema()),
        ])

    if isinstance(format_correction, dict):
        sections.extend([
            "",
            "## 仅纠正格式",
            str(format_correction.get("instruction", "")),
            "需要重新排版的原回答：",
            _json_text(format_correction.get("original_response")),
        ])
    return "\n".join(sections)


def _tool_descriptor_lines(descriptor: dict[str, Any]) -> list[str]:
    tool_id = str(descriptor.get("tool_id") or descriptor.get("name") or "")
    purpose = str(descriptor.get("description") or descriptor.get("name") or "无说明")
    schema = descriptor.get("input_schema", {})
    if not isinstance(schema, dict):
        schema = {}
    lines = [f"### `{tool_id}`", f"- 用途：{purpose}"]
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))
    if isinstance(properties, dict) and properties:
        lines.append("- 精确参数：")
        for name, definition in properties.items():
            requirement = "必填" if name in required else "可选"
            lines.append(
                f"  - `{name}`（{requirement}）：{_json_text(definition)}"
            )
        other_constraints = {
            key: value
            for key, value in schema.items()
            if key not in {"properties", "required", "type"}
        }
        if other_constraints:
            lines.append(f"- 其他参数约束：{_json_text(other_constraints)}")
    elif schema:
        lines.append(f"- 精确参数：{_json_text(schema)}")
    else:
        lines.append("- 精确参数：无")
    return lines


def _json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, default=str)


class LLMAgentRuntime(AgentRuntime):
    runtime_type = "llm"

    def __init__(
        self,
        agent_id: str,
        client: Any,
        card: Any | None = None,
        system_prompt: str = (
            "你负责完成当前消息定义的业务步骤。只使用当前可见材料和可用工具，"
            "并严格按当前输出结构返回。"
        ),
    ) -> None:
        self.agent_id = agent_id
        self.client = client
        self.card = card or {"agent_id": agent_id}
        self.system_prompt = system_prompt
        self.event_bus = None

    def set_event_bus(self, event_bus: Any | None) -> None:
        self.event_bus = event_bus

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        request_config = invocation.metadata.get("model_request_config", {})
        generation_kwargs = {
            key: request_config[key]
            for key in (
                "temperature",
                "top_p",
                "max_completion_tokens",
                "timeout",
                "retry_count",
                "retry_delay",
            )
            if key in request_config
        }
        generation_method = (
            self.client.generate_with_system
            if hasattr(self.client, "generate_with_system")
            else getattr(self.client, "generate_json", None)
        )
        visible_schema = invocation.metadata.get("visible_action_schema")
        structured_output_enforced = False
        if (
            isinstance(visible_schema, dict)
            and visible_schema
            and generation_method is not None
            and _accepts_keyword(generation_method, "response_format")
        ):
            generation_kwargs["response_format"] = visible_schema
            structured_output_enforced = True
        prompt_invocation = invocation.model_copy(update={
            "metadata": {
                **invocation.metadata,
                "structured_output_enforced": structured_output_enforced,
            }
        })
        prompt = self._build_prompt(prompt_invocation)
        span = None
        started = time.perf_counter()
        if self.event_bus is not None:
            span = self.event_bus.start_span(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                stage="llm_call",
                event_type="llm_call_started",
                actor_type="llm_runtime",
                actor_id=self.agent_id,
                message=f"LLM call started for {self.agent_id}",
                parent_span_id=invocation.metadata.get("parent_span_id"),
                node_id=str(invocation.plan_summary.get("node_id", "")),
                operation="llm.generate",
                input={
                    "system_prompt": self.system_prompt,
                    "user_prompt": prompt,
                    "model": getattr(self.client, "model", type(self.client).__name__),
                    "temperature": getattr(self.client, "temperature", None),
                },
                upstream_ids=[invocation.requester_id],
                downstream_ids=[self.agent_id],
            )
        try:
            raw_response: Any = None
            if hasattr(self.client, "generate_with_system"):
                text = self.client.generate_with_system(
                    self.system_prompt, prompt, **generation_kwargs
                )
                raw_response = text
                parsed = self._parse_possible_json(text)
                output = parsed if isinstance(parsed, dict) else {"text": text}
            elif hasattr(self.client, "generate_json"):
                output = self.client.generate_json(
                    self.system_prompt, prompt, **generation_kwargs
                )
                raw_response = output
                if not isinstance(output, dict):
                    output = {"text": output}
            else:
                raise ValueError("LLM runtime client must implement generate_with_system or generate_json")
            action = self._parse_action(output)
            self._finish_llm_span(invocation, span, started, raw_response, output, action)
            call_trace = self._build_call_trace(
                prompt=prompt,
                request_config=request_config,
                raw_response=raw_response,
                parsed_output=output,
                started=started,
            )
            if action is not None:
                if invocation.metadata.get("agentic_loop"):
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=self.agent_id,
                        output={"requested_action": output},
                        action=action,
                        metadata={
                            "runtime_type": self.runtime_type,
                            "applied_model_request_config": request_config,
                            "model_call_trace": call_trace,
                        },
                    )
                if action.type == "tool_call":
                    return await self._handle_tool_call(
                        invocation, output, action=action, call_trace=call_trace
                    )
                if action.type == "final":
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=self.agent_id,
                        output={"text": action.answer},
                        action=action,
                        metadata={
                            "runtime_type": self.runtime_type,
                            "applied_model_request_config": request_config,
                            "model_call_trace": call_trace,
                        },
                    )
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                output=output,
                action=(
                    FinalAction(answer=output.get("text", output))
                    if invocation.metadata.get("agentic_loop")
                    else None
                ),
                metadata={
                    "runtime_type": self.runtime_type,
                    "applied_model_request_config": request_config,
                    "model_call_trace": call_trace,
                },
            )
        except Exception as exc:
            if self.event_bus is not None and span is not None:
                self.event_bus.finish_span(
                    span_id=span.span_id,
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    stage="llm_call",
                    event_type="llm_call_failed",
                    actor_type="llm_runtime",
                    actor_id=self.agent_id,
                    message=str(exc),
                    node_id=str(invocation.plan_summary.get("node_id", "")),
                    operation="llm.generate",
                    phase="failed",
                    status="failed",
                    duration_ms=(time.perf_counter() - started) * 1000,
                    error=str(exc),
                )
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                status="failed",
                error=str(exc),
                metadata={
                    "runtime_type": self.runtime_type,
                    "model_call_trace": self._build_call_trace(
                        prompt=prompt,
                        request_config=request_config,
                        raw_response=None,
                        parsed_output=None,
                        started=started,
                        error=str(exc),
                    ),
                },
            )

    def _build_call_trace(
        self,
        *,
        prompt: str,
        request_config: dict[str, Any],
        raw_response: Any,
        parsed_output: Any,
        started: float,
        error: str | None = None,
    ) -> dict[str, Any]:
        provider_calls = getattr(self.client, "last_provider_calls", None) or []
        latest_provider_call = provider_calls[-1] if provider_calls else {}
        provider_request = latest_provider_call.get("request")
        if not isinstance(provider_request, dict):
            provider_request = {}
        provider_response = latest_provider_call.get("response")
        exact_messages = provider_request.get("messages")
        if not isinstance(exact_messages, list):
            exact_messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": prompt},
            ]
        return {
            "runtime_type": self.runtime_type,
            "agent_id": self.agent_id,
            "model": getattr(self.client, "model", type(self.client).__name__),
            "request": {
                "messages": exact_messages,
                "config": dict(request_config),
                "provider_payload": provider_request or None,
            },
            "response": {
                "raw": raw_response,
                "parsed": parsed_output,
                "error": error,
                "provider_payload": provider_response,
                "provider_metadata": getattr(
                    self.client, "last_response_metadata", {}
                ),
            },
            "provider_calls": provider_calls,
            "provider_request_count": len(provider_calls),
            "usage": getattr(self.client, "last_usage", None),
            "latency_ms": (
                getattr(self.client, "last_latency_ms", None)
                or (time.perf_counter() - started) * 1000
            ),
            "retry_count": getattr(self.client, "last_retry_count", 0),
            "attempts": getattr(self.client, "last_attempts", []),
        }

    def _finish_llm_span(self, invocation: AgentInvocation, span: Any, started: float,
                         raw_response: Any, output: dict[str, Any], action: AgentAction | None) -> None:
        if self.event_bus is None or span is None:
            return
        usage = getattr(self.client, "last_usage", None)
        self.event_bus.finish_span(
            span_id=span.span_id,
            task_id=invocation.task_id,
            trace_id=invocation.trace_id,
            stage="llm_call",
            event_type="llm_call_completed",
            actor_type="llm_runtime",
            actor_id=self.agent_id,
            message=f"LLM call completed for {self.agent_id}",
            node_id=str(invocation.plan_summary.get("node_id", "")),
            operation="llm.generate",
            duration_ms=(time.perf_counter() - started) * 1000,
            output={
                "raw_response": raw_response,
                "parsed_output": output,
                "agent_action": action.model_dump(mode="json") if action else None,
                "token_usage": usage,
                "retry_count": getattr(self.client, "last_retry_count", 0),
            },
            upstream_ids=[invocation.requester_id],
            downstream_ids=[self.agent_id],
        )

    def get_card(self) -> dict[str, Any]:
        if hasattr(self.card, "model_dump"):
            return self.card.model_dump(mode="json")
        if isinstance(self.card, dict):
            return self.card
        return {"agent_id": self.agent_id}

    def _build_prompt(self, invocation: AgentInvocation) -> str:
        return _build_model_prompt(invocation)

    async def _handle_tool_call(
        self,
        invocation: AgentInvocation,
        output: dict[str, Any],
        *,
        action: AgentAction | None = None,
        call_trace: dict[str, Any] | None = None,
    ) -> AgentInvocationResult:
        tool_context = invocation.metadata.get("tool_context")
        if tool_context is None:
            raise ValueError("LLM requested tool_call but no ToolExecutionContext is available")
        tool_action = action if isinstance(action, ToolAction) else self._parse_action(output)
        if not isinstance(tool_action, ToolAction):
            raise ValueError("LLM tool_call output does not contain a valid tool request")
        # AgentModelAction uses the provider-compatible nested wire shape:
        # action.tool_call.tool_id / action.tool_call.arguments.  Execute the
        # normalized ToolAction instead of looking for obsolete top-level
        # tool_id and arguments fields.
        tool_id = tool_action.tool_id
        arguments = dict(tool_action.arguments)
        result = await tool_context.call_tool(tool_id, arguments)
        return AgentInvocationResult(
            task_id=invocation.task_id,
            trace_id=invocation.trace_id,
            agent_id=self.agent_id,
            status="completed" if result.status == "completed" else "failed",
            output={"tool_result": result.output},
            tool_calls=[result.model_dump(mode="json")],
            action=tool_action,
            error=result.error if result.status != "completed" else None,
            metadata={
                "runtime_type": self.runtime_type,
                "llm_output_type": "tool_call",
                "model_call_trace": call_trace or {},
            },
        )

    @staticmethod
    def _parse_possible_json(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if not stripped.startswith("{"):
            return value
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            return value

    @staticmethod
    def _parse_action(output: dict[str, Any]) -> AgentAction | None:
        if not isinstance(output, dict):
            return None
        decision_output, decision_error = try_parse_decision_output(output)
        if decision_error is None and decision_output is not None:
            return FinalAction(
                answer=decision_output.model_dump(mode="json"),
                limitations=[],
                confidence=0.6,
            )
        candidate = output
        # First parse the evaluation protocol.  Its provider-facing schema is
        # nested under `action` and AgentModelAction expands that wire shape to
        # the internal `type` representation.  If this is not an evaluation
        # action, keep supporting the generic runtime AgentAction protocol.
        try:
            candidate = AgentModelAction.model_validate(output).model_dump(
                mode="json"
            )
        except Exception:
            candidate = _expand_nested_wire_action(output) or output
        converted = _convert_agent_model_action(candidate)
        if converted is not None:
            candidate = converted
        try:
            return _ACTION_ADAPTER.validate_python(candidate)
        except Exception:
            return None


def _expand_nested_wire_action(output: dict[str, Any]) -> dict[str, Any] | None:
    """Expand the provider nested action for the generic runtime adapter.

    AgentModelAction performs strict evaluation-field validation.  A generic
    LLMAgentRuntime may expose other governed tools whose argument names are
    not part of that evaluation-only model, so the runtime still needs to
    normalize the common nested envelope before validating AgentAction.
    """
    wire_action = output.get("action")
    if not isinstance(wire_action, dict):
        return None
    kind = wire_action.get("kind")
    if kind == "tool_call" and isinstance(wire_action.get("tool_call"), dict):
        return {
            "type": "tool_call",
            "tool_call": wire_action["tool_call"],
        }
    if kind == "final" and wire_action.get("tool_call") is None:
        return {
            "type": "final",
            "business_output": wire_action.get("business_output", {}),
            "behavior_record": wire_action.get("behavior_record", {}),
            "reason": wire_action.get("reason", ""),
        }
    return None


def _convert_agent_model_action(output: dict[str, Any]) -> dict[str, Any] | None:
    """Convert v2 AgentModelAction wire format to runtime AgentAction."""
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
