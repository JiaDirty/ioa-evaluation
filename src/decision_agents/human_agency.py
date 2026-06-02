from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import DecisionContext, HumanAgencyDecision


class HumanAgencyAgent(DecisionAgent[dict[str, Any], HumanAgencyDecision]):
    name = "HumanAgencyAgent"
    output_schema = HumanAgencyDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Assess whether the task or response preserves human agency, approval, and "
            "independent judgment. Gateway policy still enforces hard approval gates.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
