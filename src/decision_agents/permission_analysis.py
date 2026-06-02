from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import DecisionContext, PermissionAnalysisDecision


class PermissionAnalysisAgent(DecisionAgent[dict[str, Any], PermissionAnalysisDecision]):
    name = "PermissionAnalysisAgent"
    output_schema = PermissionAnalysisDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Infer required authorization scopes and human-approval sensitivity. "
            "Scopes are proposals only; deterministic policy decides authorization.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
