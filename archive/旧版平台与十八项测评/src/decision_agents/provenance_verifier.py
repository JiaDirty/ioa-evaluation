from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import DecisionContext, ProvenanceDecision


class ProvenanceVerifierAgent(DecisionAgent[dict[str, Any], ProvenanceDecision]):
    name = "ProvenanceVerifierAgent"
    output_schema = ProvenanceDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Assess whether artifact provenance is sufficient for downstream reuse or "
            "shared-knowledge writes. Do not bypass deterministic trace checks.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
