from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import DecisionContext, ProtocolSemanticsDecision


class ProtocolSemanticsAgent(DecisionAgent[dict[str, Any], ProtocolSemanticsDecision]):
    name = "ProtocolSemanticsAgent"
    output_schema = ProtocolSemanticsDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Assess whether the selected protocol preserves the task semantics, auditability, "
            "and safety constraints. The protocol negotiator still enforces hard compatibility.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
