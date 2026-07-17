"""Human-in-the-loop runtime adapter."""

from __future__ import annotations

from typing import Any

from .base import AgentInvocation, AgentInvocationResult, AgentRuntime


class HumanAgentRuntime(AgentRuntime):
    runtime_type = "human"

    def __init__(self, agent_id: str = "human", card: Any | None = None) -> None:
        self.agent_id = agent_id
        self.card = card or {
            "agent_id": agent_id,
            "display_name": "Human Operator",
            "runtime_type": self.runtime_type,
        }

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        response = (
            invocation.metadata.get("human_response")
            or invocation.context.get("human_response")
            or invocation.input.get("human_response")
        )
        if response:
            return AgentInvocationResult(
                task_id=invocation.task_id,
                trace_id=invocation.trace_id,
                agent_id=self.agent_id,
                output={"text": str(response), "approved": True},
                metadata={"runtime_type": self.runtime_type},
            )
        return AgentInvocationResult(
            task_id=invocation.task_id,
            trace_id=invocation.trace_id,
            agent_id=self.agent_id,
            status="input_required",
            output={
                "prompt": invocation.input.get("task", "Human input required"),
                "approved": False,
            },
            metadata={"runtime_type": self.runtime_type},
        )

    def get_card(self) -> dict[str, Any]:
        if hasattr(self.card, "model_dump"):
            return self.card.model_dump(mode="json")
        if isinstance(self.card, dict):
            return self.card
        return {"agent_id": self.agent_id}
