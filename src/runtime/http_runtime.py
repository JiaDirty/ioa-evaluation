"""HTTP-backed Agent runtime adapter."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

from .base import AgentInvocation, AgentInvocationResult, AgentRuntime


class HTTPAgentRuntime(AgentRuntime):
    runtime_type = "http"

    def __init__(
        self,
        agent_id: str,
        endpoint: str,
        card: Any | None = None,
        timeout_seconds: int = 30,
    ) -> None:
        self.agent_id = agent_id
        self.endpoint = endpoint
        self.card = card or {"agent_id": agent_id, "endpoint": endpoint}
        self.timeout_seconds = timeout_seconds

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        return await asyncio.to_thread(self._invoke_blocking, invocation)

    def get_card(self) -> dict[str, Any]:
        if hasattr(self.card, "model_dump"):
            return self.card.model_dump(mode="json")
        if isinstance(self.card, dict):
            return self.card
        return {"agent_id": self.agent_id, "endpoint": self.endpoint}

    def _invoke_blocking(self, invocation: AgentInvocation) -> AgentInvocationResult:
        raw = json.dumps(invocation.model_dump(mode="json"), ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=raw,
            headers={"Content-Type": "application/json", "X-Trace-Id": invocation.trace_id},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
                parsed = json.loads(body) if body else {}
                return self._normalize_result(invocation, parsed, response.status)
        except urllib.error.HTTPError as exc:
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                status="failed",
                error=f"HTTP runtime failed with {exc.code}",
                metadata={"runtime_type": self.runtime_type, "endpoint": self.endpoint},
            )
        except Exception as exc:
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                status="failed",
                error=str(exc),
                metadata={"runtime_type": self.runtime_type, "endpoint": self.endpoint},
            )

    def _normalize_result(
        self, invocation: AgentInvocation, parsed: dict[str, Any], http_status: int
    ) -> AgentInvocationResult:
        status = parsed.get("status", "completed")
        output = parsed.get("output", parsed.get("content", {}))
        if not isinstance(output, dict):
            output = {"text": output}
        metadata = dict(parsed.get("metadata", {}))
        metadata.update({
            "runtime_type": self.runtime_type,
            "endpoint": self.endpoint,
            "http_status": http_status,
        })
        return AgentInvocationResult(
            task_id=parsed.get("task_id", invocation.task_id),
            trace_id=parsed.get("trace_id", invocation.trace_id),
            agent_id=parsed.get("agent_id", self.agent_id),
            status=status,
            output=output,
            artifacts=list(parsed.get("artifacts", [])),
            tool_calls=list(parsed.get("tool_calls", [])),
            agent_calls=list(parsed.get("agent_calls", [])),
            error=parsed.get("error"),
            metadata=metadata,
        )
