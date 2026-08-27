from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import DecisionContext, RegistryRiskDecision


class RegistryRiskAgent(DecisionAgent[dict[str, Any], RegistryRiskDecision]):
    name = "RegistryRiskAgent"
    output_schema = RegistryRiskDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Assess registry mutation risk: identity spoofing, capability inflation, "
            "certificate weakness, reputation manipulation, and quarantine need.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
