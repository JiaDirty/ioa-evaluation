from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from .models import DecisionContext, DecisionEnvelope

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT", bound=BaseModel)


class DecisionAgentError(Exception):
    """Raised when a Decision Agent cannot produce valid structured output."""


class DecisionAgent(ABC, Generic[InputT, OutputT]):
    name: str = "DecisionAgent"
    output_schema: type[OutputT]
    max_tokens: int = 800

    def __init__(self, model_client, *, temperature: float = 0.0) -> None:
        self.model_client = model_client
        self.temperature = temperature

    def build_system_prompt(self, ctx: DecisionContext) -> str:
        schema_json = self.output_schema.model_json_schema()
        return (
            f"You are {self.name}, a semantic decision agent for an IoA security testbed. "
            "Return only strict JSON matching this schema. "
            "Do not authorize tasks or bypass deterministic policy. "
            f"Schema: {json.dumps(schema_json, ensure_ascii=False)}"
        )

    @abstractmethod
    def build_user_prompt(self, decision_input: InputT, ctx: DecisionContext) -> str:
        raise NotImplementedError

    def decide(self, decision_input: InputT, ctx: DecisionContext) -> OutputT:
        system = self.build_system_prompt(ctx)
        user = self.build_user_prompt(decision_input, ctx)
        raw = self.model_client.generate_with_system(
            system,
            user,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        try:
            payload = json.loads(self._strip_markdown(raw))
            return self.output_schema.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as e:
            raise DecisionAgentError(
                f"{self.name} failed to produce valid structured output: {e}"
            ) from e

    def envelope(self, output: OutputT, ctx: DecisionContext) -> DecisionEnvelope:
        return DecisionEnvelope(
            decision_id=str(uuid.uuid4())[:12],
            agent_name=self.name,
            task_id=ctx.task_id,
            trace_id=ctx.trace_id,
            stage=ctx.stage,
            confidence=getattr(output, "confidence", None),
            output=output.model_dump(mode="json"),
        )

    @staticmethod
    def _strip_markdown(raw: str) -> str:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            body = []
            in_block = False
            for line in lines:
                if line.startswith("```") and not in_block:
                    in_block = True
                    continue
                if line.startswith("```") and in_block:
                    break
                if in_block:
                    body.append(line)
            return "\n".join(body).strip()
        return text
