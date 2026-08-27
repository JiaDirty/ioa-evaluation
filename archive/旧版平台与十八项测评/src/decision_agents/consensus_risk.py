from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import ConsensusRiskDecision, DecisionContext


class ConsensusRiskAgent(DecisionAgent[dict[str, Any], ConsensusRiskDecision]):
    name = "ConsensusRiskAgent"
    output_schema = ConsensusRiskDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Assess whether the artifact or multi-agent evidence shows false consensus, "
            "source collapse, or unverified rumor reuse.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
