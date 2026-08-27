"""Adapter for existing IoA/AG2-style agent runtimes."""

from __future__ import annotations

import inspect
import hashlib
import json
import time
from typing import Any

from pydantic import TypeAdapter

from .actions import AgentAction, FinalAction, ToolAction
from .base import AgentInvocation, AgentInvocationResult, AgentRuntime
from .llm_runtime import (
    _build_model_prompt,
    _native_tool_action,
    _provider_messages,
    _provider_tools_from_descriptors,
)
from ..evaluation.agent_model.behavior_parser import try_parse_decision_output
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
        run_task = self.ioa_agent.run_task
        native_tool_protocol = bool(
            invocation.metadata.get("native_tool_protocol")
        )
        run_task_parameters = inspect.signature(run_task).parameters.values()
        supports_request_config = any(
            parameter.name == "model_request_config"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in run_task_parameters
        )
        request_config = dict(invocation.metadata.get("model_request_config", {}))
        effective_request_config = dict(request_config)
        visible_schema = invocation.metadata.get("visible_action_schema")
        response_schema_hash = ""
        if supports_request_config and isinstance(visible_schema, dict) and visible_schema:
            effective_request_config["response_format"] = visible_schema
            response_schema_hash = hashlib.sha256(
                json.dumps(
                    visible_schema,
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                ).encode("utf-8")
            ).hexdigest()
        prompt_invocation = invocation.model_copy(update={
            "metadata": {
                **invocation.metadata,
                "structured_output_enforced": bool(
                    getattr(self.ioa_agent, "structured_output_schema", None)
                ) or bool(response_schema_hash),
            }
        })
        prompt = self._build_prompt(prompt_invocation)
        max_turns = int(invocation.metadata.get("max_turns", self.default_max_turns))
        applied_request_config = request_config if supports_request_config else {}
        started = time.perf_counter()
        try:
            provider_assistant_message = None
            provider_tool_call = None
            parsed_response: Any = None
            if native_tool_protocol:
                run_provider_turn = getattr(
                    self.ioa_agent, "run_provider_turn", None
                )
                if not callable(run_provider_turn):
                    raise ValueError(
                        "native tool protocol requires run_provider_turn"
                    )
                provider_descriptors = invocation.metadata.get(
                    "provider_tool_descriptors",
                    invocation.available_tool_descriptors,
                )
                if not isinstance(provider_descriptors, list):
                    provider_descriptors = []
                turn = run_provider_turn(
                    _provider_messages(
                        invocation,
                        str(getattr(self.ioa_agent, "system_message", "")),
                        prompt,
                    ),
                    tools=_provider_tools_from_descriptors(
                        provider_descriptors
                    ),
                    tool_choice=invocation.metadata.get(
                        "provider_tool_choice", "auto"
                    ),
                    parallel_tool_calls=False,
                    response_format=visible_schema,
                    model_request_config=request_config,
                )
                if not isinstance(turn, dict):
                    raise ValueError("run_provider_turn must return a mapping")
                provider_assistant_message = turn.get("assistant_message")
                action, provider_tool_call, native_error = _native_tool_action(
                    turn
                )
                if native_error:
                    raise ValueError(native_error)
                if action is not None:
                    parsed_response = {
                        "type": "tool_call",
                        "tool_call": {
                            "tool_id": action.tool_id,
                            "arguments": action.arguments,
                            "reason": action.reason,
                        },
                    }
                    result = parsed_response
                else:
                    content = turn.get("content")
                    if not isinstance(content, str):
                        raise ValueError(
                            "provider returned neither a tool call nor final text"
                        )
                    result = content
                    parsed_response = self._parse_json_value(content)
                    action = self._parse_action(parsed_response)
            elif supports_request_config:
                result = run_task(
                    prompt,
                    max_turns=max_turns,
                    model_request_config=effective_request_config,
                )
            else:
                result = run_task(prompt, max_turns=max_turns)
            if inspect.isawaitable(result):
                result = await result
            if not native_tool_protocol:
                action = self._parse_action(result)
                parsed_response = result
            if action is None and invocation.metadata.get("agentic_loop"):
                action = FinalAction(
                    answer=result,
                    limitations=["AG2 runtime returned text; wrapped as FinalAction by the controlled adapter."],
                    confidence=0.6,
                )
            call_trace = self._build_call_trace(
                prompt=prompt,
                applied_request_config=applied_request_config,
                response_schema=visible_schema,
                response_schema_hash=response_schema_hash,
                raw_response=result,
                parsed_response=parsed_response,
                error=None,
                started=started,
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
                    "applied_model_request_config": applied_request_config,
                    "model_call_trace": call_trace,
                    "provider_assistant_message": provider_assistant_message,
                    "provider_tool_call": provider_tool_call,
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
                    "model_call_trace": self._build_call_trace(
                        prompt=prompt,
                        applied_request_config=applied_request_config,
                        response_schema=visible_schema,
                        response_schema_hash=response_schema_hash,
                        raw_response=None,
                        parsed_response=None,
                        error=str(exc),
                        started=started,
                    ),
                    "applied_model_request_config": applied_request_config,
                },
            )

    def _build_call_trace(
        self,
        *,
        prompt: str,
        applied_request_config: dict[str, Any],
        response_schema: Any,
        response_schema_hash: str,
        raw_response: Any,
        parsed_response: Any,
        error: str | None,
        started: float,
    ) -> dict[str, Any]:
        provider_calls = getattr(self.ioa_agent, "last_provider_calls", None) or []
        latest_provider_call = provider_calls[-1] if provider_calls else {}
        provider_request = latest_provider_call.get("request")
        if not isinstance(provider_request, dict):
            provider_request = {}
        request_payload = provider_request.get("kwargs", provider_request)
        if not isinstance(request_payload, dict):
            request_payload = {}
        provider_response = latest_provider_call.get("response")
        exact_messages = request_payload.get("messages")
        if not isinstance(exact_messages, list):
            exact_messages = [{"role": "user", "content": prompt}]
        return {
            "runtime_type": self.runtime_type,
            "agent_id": self.agent_id,
            "model": getattr(
                self.ioa_agent, "model", type(self.ioa_agent).__name__
            ),
            "request": {
                "messages": exact_messages,
                "config": applied_request_config,
                "response_schema": (
                    response_schema if isinstance(response_schema, dict) else None
                ),
                "response_schema_hash": response_schema_hash or None,
                "provider_payload": request_payload or None,
            },
            "response": {
                "raw": raw_response,
                "parsed": parsed_response,
                "error": error,
                "provider_payload": provider_response,
            },
            "provider_calls": provider_calls,
            "provider_request_count": len(provider_calls),
            "usage": getattr(self.ioa_agent, "last_usage", None),
            "latency_ms": (time.perf_counter() - started) * 1000,
            "retry_count": getattr(self.ioa_agent, "last_retry_count", 0),
            "response_metadata": getattr(
                self.ioa_agent, "last_response_metadata", None
            ),
        }

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
        return _build_model_prompt(invocation)

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
        if not isinstance(payload, dict):
            return None
        decision_output, decision_error = try_parse_decision_output(payload)
        if decision_error is None and decision_output is not None:
            return FinalAction(
                answer=decision_output.model_dump(mode="json"),
                limitations=[],
                confidence=0.6,
            )
        if not ({"type", "action"} & payload.keys()):
            return None
        try:
            payload = AgentModelAction.model_validate(payload).model_dump(mode="json")
        except Exception:
            return None
        converted = _convert_agent_model_action(payload)
        if converted is not None:
            payload = converted
        try:
            return _ACTION_ADAPTER.validate_python(payload)
        except Exception:
            return None

    @staticmethod
    def _parse_json_value(value: str) -> Any:
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value


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
