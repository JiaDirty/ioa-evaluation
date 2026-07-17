"""Tool Gateway for local and future MCP-backed tools."""

from __future__ import annotations

import inspect
import time
from typing import Any

from .models import ToolCall, ToolResult
from .policies import ToolPolicyEngine
from .registry import ToolRegistry


class ToolGateway:
    def __init__(
        self,
        registry: ToolRegistry | None = None,
        policy_engine: ToolPolicyEngine | None = None,
        tool_call_store: Any | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.policy_engine = policy_engine or ToolPolicyEngine()
        self.providers: dict[str, Any] = {}
        self.tool_call_store = tool_call_store
        self._history: list[ToolResult] = []
        self.event_bus = None

    def set_event_bus(self, event_bus: Any | None) -> None:
        self.event_bus = event_bus

    def register_provider(self, provider_name: str, provider: Any) -> None:
        self.providers[provider_name] = provider

    def set_tool_call_store(self, tool_call_store: Any) -> None:
        self.tool_call_store = tool_call_store

    async def call_tool(self, call: ToolCall) -> ToolResult:
        span = None
        started = time.perf_counter()
        if self.event_bus is not None:
            span = self.event_bus.start_span(
                task_id=call.task_id,
                trace_id=call.trace_id or call.task_id,
                stage="tool_call",
                event_type="tool_call_started",
                actor_type="tool_gateway",
                actor_id=call.tool_id,
                message=f"Tool call started: {call.tool_id}",
                parent_span_id=call.parent_span_id,
                operation="tool.call",
                input={"arguments": call.arguments, "granted_scopes": call.granted_scopes},
                upstream_ids=[call.caller_agent_id],
                downstream_ids=[call.tool_id],
            )
        descriptor = self.registry.get(call.tool_id)
        if descriptor is None:
            result = ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="failed",
                error=f"unknown tool: {call.tool_id}",
            )
            self._record_result(call, result)
            self._finish_observation(call, result, span, started)
            return result

        allowed, reason = self.policy_engine.authorize(descriptor, call)
        if not allowed:
            result = ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="denied",
                error=reason,
                metadata={"descriptor": descriptor.model_dump(mode="json")},
            )
            self._record_result(call, result)
            self._finish_observation(call, result, span, started)
            return result

        schema_error = _validate_input_schema(descriptor.input_schema, call.arguments)
        if schema_error:
            result = ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="failed",
                error=f"input schema validation failed: {schema_error}",
                metadata={"provider": descriptor.provider, "risk_level": descriptor.risk_level},
            )
            self._record_result(call, result)
            self._finish_observation(call, result, span, started)
            return result

        try:
            if descriptor.provider == "local":
                handler = self.registry.get_handler(call.tool_id)
                if handler is None:
                    result = ToolResult(
                        call_id=call.call_id,
                        tool_id=call.tool_id,
                        status="failed",
                        error=f"tool has no local handler: {call.tool_id}",
                    )
                    self._record_result(call, result)
                    self._finish_observation(call, result, span, started)
                    return result
                output: Any = handler(**call.arguments)
                if inspect.isawaitable(output):
                    output = await output
                result = ToolResult(
                    call_id=call.call_id,
                    tool_id=call.tool_id,
                    output=output,
                    metadata={"provider": descriptor.provider, "risk_level": descriptor.risk_level},
                )
            else:
                provider = self.providers.get(descriptor.provider)
                if provider is None:
                    result = ToolResult(
                        call_id=call.call_id,
                        tool_id=call.tool_id,
                        status="failed",
                        error=f"tool provider not registered: {descriptor.provider}",
                    )
                    self._record_result(call, result)
                    self._finish_observation(call, result, span, started)
                    return result
                result = await provider.call(descriptor, call.arguments)
                result = result.model_copy(update={"call_id": call.call_id, "tool_id": call.tool_id})
        except Exception as exc:
            result = ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status="failed",
                error=str(exc),
            )
        self._record_result(call, result)
        self._finish_observation(call, result, span, started)
        return result

    def _finish_observation(self, call: ToolCall, result: ToolResult, span: Any, started: float) -> None:
        if self.event_bus is None or span is None:
            return
        failed = result.status != "completed"
        self.event_bus.finish_span(
            span_id=span.span_id,
            task_id=call.task_id,
            trace_id=call.trace_id or call.task_id,
            stage="tool_call",
            event_type="tool_call_failed" if failed else "tool_call_completed",
            actor_type="tool_gateway",
            actor_id=call.tool_id,
            message=result.error or f"Tool call completed: {call.tool_id}",
            operation="tool.call",
            phase="failed" if failed else "completed",
            status=result.status,
            duration_ms=(time.perf_counter() - started) * 1000,
            output=result.model_dump(mode="json"),
            upstream_ids=[call.caller_agent_id],
            downstream_ids=[call.tool_id],
            error=result.error,
        )

    def list_tools(self) -> list[dict[str, Any]]:
        return [tool.model_dump(mode="json") for tool in self.registry.list_tools()]

    def get_tool(self, tool_id: str) -> dict[str, Any] | None:
        descriptor = self.registry.get(tool_id)
        return descriptor.model_dump(mode="json") if descriptor else None

    def history(self) -> list[ToolResult]:
        return list(self._history)

    def _record_result(self, call: ToolCall, result: ToolResult) -> None:
        self._history.append(result)
        if self.tool_call_store is not None:
            self.tool_call_store.append_result(call, result)


def _validate_input_schema(schema: dict[str, Any], arguments: dict[str, Any]) -> str | None:
    if not schema:
        return None
    schema_type = schema.get("type")
    if schema_type and schema_type != "object":
        return "only object input schemas are supported"
    required = [str(item) for item in schema.get("required", [])]
    for field in required:
        if field not in arguments:
            return f"missing required argument: {field}"
    properties = schema.get("properties", {})
    if not isinstance(properties, dict):
        return None
    for name, spec in properties.items():
        if name not in arguments or not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if expected and not _matches_json_type(arguments[name], str(expected)):
            return f"argument {name} must be {expected}"
    return None


def _matches_json_type(value: Any, expected: str) -> bool:
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return (isinstance(value, int) or isinstance(value, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    return True
