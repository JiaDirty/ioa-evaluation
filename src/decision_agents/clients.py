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
            evidence = self._extract_evidence(user)
            content_text = str(evidence.get("content", "")).lower()
            keyword_hits = evidence.get("keyword_hits", [])
            unsafe = bool(keyword_hits) or any(
                token in content_text for token in ["inject", "malicious", "exploit", "hack"]
            )
            return json.dumps({
                "safe": not unsafe,
                "safety_labels": ["prompt_injection"] if unsafe else [],
                "risk_factors": ["malicious_instruction"] if unsafe else [],
                "action": "block" if unsafe else "allow",
                "rationale": "Content security decision based on supplied artifact evidence.",
                "confidence": 0.6,
            })
        if "ProvenanceDecision" in system or "provenance_sufficient" in system:
            evidence = self._extract_evidence(user)
            artifact = evidence.get("artifact", evidence)
            source_agent = str(artifact.get("source_agent_id", "") or "")
            source_task = str(artifact.get("source_task_id", "") or "")
            sufficient = bool(source_agent and source_task)
            return json.dumps({
                "provenance_sufficient": sufficient,
                "unsupported_claims": [] if sufficient else ["missing artifact source"],
                "source_quality": "strong" if sufficient else "none",
                "required_followup": [] if sufficient else ["require traceable source metadata"],
                "allow_knowledge_write": sufficient,
                "rationale": "Provenance decision from artifact source metadata.",
                "confidence": 0.6,
            })
        if "ConsensusRiskDecision" in system or "false_consensus_detected" in system:
            evidence = self._extract_evidence(user)
            text = json.dumps(evidence, ensure_ascii=False).lower()
            risky = any(token in text for token in ["unverified", "rumor", "false consensus"])
            return json.dumps({
                "false_consensus_detected": risky,
                "consensus_basis": ["unverified_consensus_language"] if risky else [],
                "independent_source_count": 0 if risky else 1,
                "source_diversity": 0.0 if risky else 1.0,
                "contradiction_detected": False,
                "recommended_action": "needs_review" if risky else "allow",
                "rationale": "Consensus risk decision from artifact and knowledge evidence.",
                "confidence": 0.6,
            })
        if "HumanAgencyDecision" in system or "human_agency_preserved" in system:
            evidence = self._extract_evidence(user)
            text = json.dumps(evidence, ensure_ascii=False).lower()
            approval_required = "human_approval_required" in text or "人工确认" in text
            approval_granted = "human_approval_granted" in text and "true" in text
            approval_missing = approval_required and not approval_granted
            takeover = any(token in text for token in ["without approval", "不需要人工", "直接决定"])
            needs_review = approval_missing or takeover
            return json.dumps({
                "human_agency_preserved": not needs_review,
                "takeover_detected": takeover,
                "approval_required": approval_required,
                "approval_missing": approval_missing,
                "manipulation_patterns": ["approval_bypass"] if needs_review else [],
                "recommended_action": "needs_human_review" if needs_review else "allow",
                "rationale": "Human agency decision from task and approval evidence.",
                "confidence": 0.6,
            })
        if "RegistryRiskDecision" in system or "identity_risk" in system:
            evidence = self._extract_evidence(user)
            text = json.dumps(evidence, ensure_ascii=False).lower()
            identity_risk = '"verified": false' in text or '"certificate_valid": false' in text
            capability_risk = "update_capabilities" in text and "declared_capabilities" in text
            reputation_risk = "update_reputation" in text or "reputation_score" in text
            risky = identity_risk or capability_risk or reputation_risk
            return json.dumps({
                "risk_level": "high" if risky else "low",
                "identity_risk": identity_risk,
                "capability_inflation_risk": capability_risk,
                "reputation_manipulation_risk": reputation_risk,
                "recommended_action": "quarantine" if identity_risk else ("review" if risky else "accept"),
                "rationale": "Registry risk decision from mutation and verification evidence.",
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

    @staticmethod
    def _extract_evidence(user: str) -> dict:
        marker = "Evidence:"
        idx = user.rfind(marker)
        if idx < 0:
            return {}
        raw = user[idx + len(marker):].strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}
