"""LLM-backed Agent runtime adapter."""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import TypeAdapter

from .actions import AgentAction, FinalAction, ToolAction
from .base import AgentInvocation, AgentInvocationResult, AgentRuntime
from ..evaluation.agent_model.models import AgentModelAction


_ACTION_ADAPTER = TypeAdapter(AgentAction)


class LLMAgentRuntime(AgentRuntime):
    runtime_type = "llm"

    def __init__(
        self,
        agent_id: str,
        client: Any,
        card: Any | None = None,
        system_prompt: str = "You are an IoA runtime agent. Return a concise task result.",
    ) -> None:
        self.agent_id = agent_id
        self.client = client
        self.card = card or {"agent_id": agent_id}
        self.system_prompt = system_prompt
        self.event_bus = None

    def set_event_bus(self, event_bus: Any | None) -> None:
        self.event_bus = event_bus

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        prompt = self._build_prompt(invocation)
        request_config = invocation.metadata.get("model_request_config", {})
        generation_kwargs = {
            key: request_config[key]
            for key in (
                "temperature",
                "top_p",
                "max_tokens",
                "timeout",
                "retry_count",
                "retry_delay",
            )
            if key in request_config
        }
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
        return {
            "runtime_type": self.runtime_type,
            "agent_id": self.agent_id,
            "model": getattr(self.client, "model", type(self.client).__name__),
            "request": {
                "messages": [
                    {"role": "system", "content": self.system_prompt},
                    {"role": "user", "content": prompt},
                ],
                "config": dict(request_config),
            },
            "response": {
                "raw": raw_response,
                "parsed": parsed_output,
                "error": error,
                "provider_metadata": getattr(
                    self.client, "last_response_metadata", {}
                ),
            },
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
        task = invocation.input.get("task") or invocation.input.get("description") or ""
        prompt = invocation.input.get("prompt", "")
        expected_output = invocation.input.get("expected_output", "")
        payload = invocation.input.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        visible_payload = {}
        if not payload.get("controlled_agent_model_evaluation_step"):
            visible_payload = {
                key: value
                for key, value in payload.items()
                if key not in {"risk_type", "variant", "evaluation_metadata"}
            }
        public_state = invocation.context.get("public_state", {})
        role_state = invocation.context.get("role_state", {})
        tool_ids = [
            str(tool.get("tool_id") or tool.get("name"))
            for tool in invocation.available_tool_descriptors
        ]

        sections = [
            f"Task ID: {invocation.task_id}",
            f"Trace ID: {invocation.trace_id}",
            f"Requester: {invocation.requester_id}",
            "",
            "## Task",
            str(task),
        ]
        if prompt:
            sections.extend(["", "## Prompt", str(prompt)])
        if expected_output:
            sections.extend(["", "## Expected Output", str(expected_output)])
        if visible_payload:
            sections.extend([
                "",
                "## Visible Payload",
                json.dumps(visible_payload, ensure_ascii=False, default=str),
            ])
        if invocation.input_artifacts:
            sections.extend([
                "",
                "## Input Artifacts",
                json.dumps(invocation.input_artifacts, ensure_ascii=False, default=str),
            ])
        if public_state:
            sections.extend([
                "",
                "## Public State",
                json.dumps(public_state, ensure_ascii=False, default=str),
            ])
        if role_state:
            sections.extend([
                "",
                "## Role State",
                json.dumps(role_state, ensure_ascii=False, default=str),
            ])
        if invocation.turn_history:
            sections.extend([
                "",
                "## Turn History",
                json.dumps(invocation.turn_history, ensure_ascii=False, default=str),
            ])
        sections.extend([
            "",
            f"Permissions: {invocation.permissions}",
            f"Available tools: {tool_ids}",
            (
                "Return JSON matching this AgentModelAction schema. "
                "Do not include evaluation labels or ground truth. "
            ),
            json.dumps(
                invocation.metadata.get("visible_action_schema")
                or AgentModelAction.model_json_schema(),
                ensure_ascii=False,
            ),
            "For tool use, return type=tool_call with tool_call.tool_id and "
            "tool_call.arguments. For final answers, return type=final with "
            "business_output and behavior_record.",
            "Do not include tool_call in a final answer. If a tool is needed, "
            "return only type=tool_call and wait for the tool result before "
            "the final answer.",
        ])
        format_correction = invocation.metadata.get("format_correction")
        if isinstance(format_correction, dict):
            sections.extend([
                "",
                "## Format-only correction",
                str(format_correction.get("instruction", "")),
                "Original response to reformat:",
                json.dumps(
                    format_correction.get("original_response"),
                    ensure_ascii=False,
                    default=str,
                ),
            ])
        return "\n".join(sections)

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
        tool_id = str(output.get("tool_id", ""))
        arguments = dict(output.get("arguments", {}))
        result = await tool_context.call_tool(tool_id, arguments)
        return AgentInvocationResult(
            task_id=invocation.task_id,
            trace_id=invocation.trace_id,
            agent_id=self.agent_id,
            status="completed" if result.status == "completed" else "failed",
            output={"tool_result": result.output},
            tool_calls=[result.model_dump(mode="json")],
            action=action,
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
        if not isinstance(output, dict) or "type" not in output:
            return None
        converted = _convert_agent_model_action(output)
        if converted is not None:
            output = converted
        try:
            return _ACTION_ADAPTER.validate_python(output)
        except Exception:
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
