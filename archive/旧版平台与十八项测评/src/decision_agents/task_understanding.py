from __future__ import annotations

import json
from typing import Any

from .base import DecisionAgent
from .models import DecisionContext, TaskUnderstandingDecision


class TaskUnderstandingAgent(DecisionAgent[dict[str, Any], TaskUnderstandingDecision]):
    name = "TaskUnderstandingAgent"
    output_schema = TaskUnderstandingDecision

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            "Analyze the task intent, domain, sensitivity, and risk factors. "
            "Do not approve execution; only provide semantic interpretation.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )
