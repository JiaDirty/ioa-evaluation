from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import CapabilityMatchDecision, DecisionContext


class CapabilityMatchingAgent(DecisionAgent[dict[str, Any], CapabilityMatchDecision]):
    name = "CapabilityMatchingAgent"
    output_schema = CapabilityMatchDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Rank already verified candidate agents by semantic fit to the required capabilities. "
            "Do not add candidates that are not present in the evidence.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
