"""LLM-backed Agent runtime adapter."""

from __future__ import annotations

import json
import time
from typing import Any

from pydantic import TypeAdapter

from .actions import AgentAction, FinalAction
from .base import AgentInvocation, AgentInvocationResult, AgentRuntime


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
                text = self.client.generate_with_system(self.system_prompt, prompt)
                raw_response = text
                parsed = self._parse_possible_json(text)
                output = parsed if isinstance(parsed, dict) else {"text": text}
            elif hasattr(self.client, "generate_json"):
                output = self.client.generate_json(self.system_prompt, prompt)
                raw_response = output
                if not isinstance(output, dict):
                    output = {"text": output}
            else:
                raise ValueError("LLM runtime client must implement generate_with_system or generate_json")
            action = self._parse_action(output)
            self._finish_llm_span(invocation, span, started, raw_response, output, action)
            if action is not None:
                if invocation.metadata.get("agentic_loop"):
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=self.agent_id,
                        output={"requested_action": output},
                        action=action,
                        metadata={"runtime_type": self.runtime_type},
                    )
                if action.type == "tool_call":
                    return await self._handle_tool_call(invocation, output, action=action)
                if action.type == "final":
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=self.agent_id,
                        output={"text": action.answer},
                        action=action,
                        metadata={"runtime_type": self.runtime_type},
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
                metadata={"runtime_type": self.runtime_type},
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
                metadata={"runtime_type": self.runtime_type},
            )

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
        tool_context = invocation.metadata.get("tool_context")
        available_tools: list[str] = []
        if tool_context is not None:
            available_tools = [
                tool["tool_id"]
                for tool in tool_context.gateway.list_tools()
            ]
        return (
            f"Task ID: {invocation.task_id}\n"
            f"Trace ID: {invocation.trace_id}\n"
            f"Requester: {invocation.requester_id}\n"
            f"Input: {invocation.input}\n"
            f"Context: {invocation.context}\n"
            f"Permissions: {invocation.permissions}\n"
            f"Available tools: {available_tools}\n"
            "Return JSON for exactly one AgentAction. Examples: "
            '{"type":"final","answer":"...","artifact_type":"text_answer",'
            '"evidence_artifact_ids":[],"limitations":[],"confidence":0.7} '
            'or {"type":"tool_call","tool_id":"...","arguments":{},"reason":"..."} '
            'or {"type":"delegate","objective":"...","required_capabilities":[],'
            '"requested_scopes":[],"input_artifact_ids":[],"expected_output":"",'
            '"reason":"..."} or {"type":"ask_user","question":"...","reason":"..."} '
            'or {"type":"replan","reason":"...","new_facts":[],"blocked_requirements":[]}.\n'
        )

    async def _handle_tool_call(
        self,
        invocation: AgentInvocation,
        output: dict[str, Any],
        *,
        action: AgentAction | None = None,
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
            metadata={"runtime_type": self.runtime_type, "llm_output_type": "tool_call"},
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
        try:
            return _ACTION_ADAPTER.validate_python(output)
        except Exception:
            return None
