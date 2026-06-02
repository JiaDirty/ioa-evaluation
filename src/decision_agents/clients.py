from __future__ import annotations

import json
import re


class DeterministicDecisionClient:
    """Offline deterministic client for unit tests and explicit fallback paths.

    It mirrors the ModelClient interface but does not claim semantic LLM reasoning.
    Live evaluations should inject an OpenAI-compatible client.
    """

    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        if "PermissionAnalysisDecision" in system or "required_scopes" in system:
            requires_approval = (
                "human_approval_required" in user
                or "high impact" in user.lower()
                or "critical decision" in user.lower()
            )
            return json.dumps({
                "required_scopes": ["execute"],
                "optional_scopes": [],
                "forbidden_scopes": [],
                "sensitivity": "high" if requires_approval else "medium",
                "requires_human_approval": requires_approval,
                "approval_reason": (
                    "High-impact action requires explicit approval"
                    if requires_approval else None
                ),
                "risk_factors": ["human_approval"] if requires_approval else [],
                "rationale": "Structured deterministic decision for offline verification.",
                "confidence": 0.6,
            })
        if "CapabilityMatchDecision" in system or "ranked_agent_ids" in system:
            agent_ids = self._extract_agent_ids(user)
            return json.dumps({
                "ranked_agent_ids": agent_ids,
                "rejected_agent_ids": [],
                "rationale_by_agent": {
                    agent_id: "Candidate retained after deterministic evidence parsing."
                    for agent_id in agent_ids
                },
                "uncertainty": 0.0,
                "confidence": 0.6,
            })
        if "ProtocolSemanticsDecision" in system or "semantic_fit" in system:
            selected = self._extract_selected_protocol(user)
            return json.dumps({
                "selected_protocol": selected,
                "semantic_fit": "compatible" if selected == "a2a" else "lossy",
                "downgrade_detected": selected == "private_api",
                "lost_semantics": [] if selected == "a2a" else ["audit_correlation"],
                "required_compensating_controls": (
                    [] if selected == "a2a" else ["explicit_audit_correlation"]
                ),
                "rationale": "Protocol semantics decision from available protocol evidence.",
                "confidence": 0.6,
            })
        if "ContentSecurityDecision" in system or "safety_labels" in system:
            text = user.lower()
            unsafe = any(token in text for token in ["inject", "malicious", "exploit", "hack"])
            return json.dumps({
                "safe": not unsafe,
                "safety_labels": ["prompt_injection"] if unsafe else [],
                "risk_factors": ["malicious_instruction"] if unsafe else [],
                "action": "block" if unsafe else "allow",
                "rationale": "Content security decision based on supplied artifact evidence.",
                "confidence": 0.6,
            })
        return json.dumps({
            "intent": "task_execution",
            "domain": self._infer_domain(user),
            "sensitivity": "medium",
            "risk_factors": [],
            "requires_human_approval": False,
            "rationale": "Task understanding decision from task evidence.",
            "confidence": 0.6,
        })

    @staticmethod
    def _extract_agent_ids(user: str) -> list[str]:
        matches = re.findall(r"'agent_id': '([^']+)'|\"agent_id\": \"([^\"]+)\"", user)
        agent_ids: list[str] = []
        for single_quoted, double_quoted in matches:
            agent_id = single_quoted or double_quoted
            if agent_id and agent_id not in agent_ids:
                agent_ids.append(agent_id)
        return agent_ids

    @staticmethod
    def _extract_selected_protocol(user: str) -> str:
        match = re.search(r'"selected_protocol"\s*:\s*"([^"]+)"', user)
        if match:
            return match.group(1)
        return "a2a" if "a2a" in user.lower() else "private_api"

    @staticmethod
    def _infer_domain(user: str) -> str:
        text = user.lower()
        if "finance" in text or "investment" in text:
            return "finance"
        if "patient" in text or "medical" in text or "health" in text:
            return "healthcare"
        if "news" in text or "rumor" in text:
            return "news"
        if "travel" in text or "flight" in text:
            return "travel"
        return "unknown"
