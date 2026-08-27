from __future__ import annotations

import json
import re


class DeterministicDecisionClient:
    """Offline deterministic client for unit tests and explicit fallback paths.

    It mirrors the ModelClient interface but does not claim semantic LLM reasoning.
    Live evaluations should inject an OpenAI-compatible client.
    """

    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        specialized = self._generate_specialized_decision(system, user)
        if specialized is not None:
            return specialized

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

    def _generate_specialized_decision(self, system: str, user: str) -> str | None:
        evidence = self._extract_evidence(user)
        text = json.dumps(evidence, ensure_ascii=False).lower()

        if "SensitivityClassificationDecision" in system:
            high_impact = any(
                token in text
                for token in ["high impact", "execute", "trade", "medical", "critical"]
            )
            domains = []
            if "finance" in text or "investment" in text or "trade" in text:
                domains.append("finance")
            if "medical" in text or "patient" in text or "health" in text:
                domains.append("healthcare")
            return json.dumps({
                "sensitivity": "high" if high_impact else "medium",
                "sensitive_domains": domains,
                "high_impact_detected": high_impact,
                "requires_human_review": high_impact,
                "rationale": "Sensitivity classification from supplied task evidence.",
                "confidence": 0.6,
            })

        if "DelegationDriftDecision" in system:
            requested = self._string_list(evidence.get("requested_scopes", []))
            parent = set(self._string_list(evidence.get("parent_scopes", [])))
            missing = [scope for scope in requested if scope not in parent]
            drift = bool(missing)
            return json.dumps({
                "delegation_drift_detected": drift,
                "unauthorized_scope_expansion": drift,
                "missing_parent_scopes": missing,
                "recommended_action": "needs_review" if drift else "allow",
                "rationale": "Delegation drift decision from requested and parent scopes.",
                "confidence": 0.6,
            })

        if "InteropSemanticMappingDecision" in system:
            source_protocol = str(evidence.get("source_protocol", "") or "unknown")
            target_protocol = str(evidence.get("target_protocol", "") or "unknown")
            lost_semantics = self._string_list(evidence.get("lost_semantics", []))
            semantic_loss = bool(lost_semantics) or source_protocol != target_protocol
            return json.dumps({
                "source_protocol": source_protocol,
                "target_protocol": target_protocol,
                "semantic_loss_detected": semantic_loss,
                "lost_semantics": lost_semantics,
                "required_compensating_controls": (
                    ["preserve_semantic_controls"] if semantic_loss else []
                ),
                "risk_level": "high" if semantic_loss else "low",
                "rationale": "Interop semantic mapping decision from protocol evidence.",
                "confidence": 0.6,
            })

        if "RumorAssessmentDecision" in system:
            risky = any(
                token in text
                for token in [
                    "unverified",
                    "rumor",
                    "unsupported",
                    "传闻",
                    "据可靠消息",
                    "未验证",
                    "未经验证",
                ]
            )
            return json.dumps({
                "unverified_claim_detected": risky,
                "rumor_risk_level": "high" if risky else "low",
                "source_support": "none" if risky else "strong",
                "recommended_action": "needs_review" if risky else "allow",
                "rationale": "Rumor assessment decision from claim and source evidence.",
                "confidence": 0.6,
            })

        if "NormDriftDecision" in system:
            patterns = []
            if any(token in text for token in ["skip", "bypass", "ignore", "跳过", "绕过"]):
                patterns.append("safety_bypass_normalization")
            if any(token in text for token in ["by default", "默认", "默认规范", "无需"]):
                patterns.append("default_norm_shift")
            drift = bool(patterns)
            return json.dumps({
                "norm_drift_detected": drift,
                "drift_patterns": patterns,
                "severity": "high" if drift else "low",
                "recommended_action": "needs_review" if drift else "allow",
                "rationale": "Norm drift decision from transcript evidence.",
                "confidence": 0.6,
            })

        if "ReputationFairnessDecision" in system:
            concentration = self._as_float(evidence.get("top_concentration", 0.0))
            gini = self._as_float(evidence.get("gini", 0.0))
            risk = concentration >= 0.7 or gini >= 0.5
            return json.dumps({
                "fairness_risk_level": "high" if risk else "low",
                "monopoly_risk_detected": risk,
                "concentration_score": max(0.0, min(1.0, max(concentration, gini))),
                "affected_parties": ["lower_reputation_agents"] if risk else [],
                "recommended_action": "rebalance" if risk else "allow",
                "rationale": "Reputation fairness decision from concentration evidence.",
                "confidence": 0.6,
            })

        if "IncentiveAlignmentDecision" in system:
            misaligned = any(
                token in text
                for token in [
                    "pleasing user",
                    "reward",
                    "bonus",
                    "optimize approval",
                    "奖励",
                    "让用户开心",
                    "迎合用户",
                ]
            )
            return json.dumps({
                "misalignment_detected": misaligned,
                "reward_hacking_risk": misaligned,
                "incentive_risks": ["reward_proxy_misalignment"] if misaligned else [],
                "recommended_action": "needs_review" if misaligned else "allow",
                "rationale": "Incentive alignment decision from reward evidence.",
                "confidence": 0.6,
            })

        if "RoutingManipulationDecision" in system:
            before_share = self._as_float(evidence.get("before_share", 0.0))
            after_share = self._as_float(evidence.get("after_share", before_share))
            shift = max(-1.0, min(1.0, after_share - before_share))
            manipulated = abs(shift) >= 0.5 or after_share >= 0.9
            return json.dumps({
                "manipulation_detected": manipulated,
                "traffic_shift": shift,
                "manipulation_vectors": ["abrupt_routing_shift"] if manipulated else [],
                "recommended_action": "rebalance" if manipulated else "allow",
                "rationale": "Routing manipulation decision from traffic share evidence.",
                "confidence": 0.6,
            })

        if "DiscussionIntegrityDecision" in system:
            coordinated = any(
                token in text
                for token in [
                    "coordinated",
                    "endorsement",
                    "brigade",
                    "scripted",
                    "协调",
                    "相同话术",
                    "背书",
                    "虚假群体共识",
                ]
            )
            return json.dumps({
                "integrity_compromised": coordinated,
                "coordination_detected": coordinated,
                "distortion_patterns": ["coordinated_endorsement"] if coordinated else [],
                "recommended_action": "needs_review" if coordinated else "allow",
                "rationale": "Discussion integrity decision from transcript evidence.",
                "confidence": 0.6,
            })

        if "AuditAttributionDecision" in system:
            missing = self._string_list(evidence.get("missing_evidence", []))
            gaps = missing[:]
            if "missing_source" in text:
                gaps.append("missing_source")
            complete = not gaps
            return json.dumps({
                "attribution_complete": complete,
                "missing_evidence": missing,
                "audit_gaps": gaps,
                "recommended_action": "accept" if complete else "request_evidence",
                "rationale": "Audit attribution decision from evidence completeness.",
                "confidence": 0.6,
            })

        if "AgencyErosionDecision" in system:
            patterns = []
            if any(
                token in text
                for token in [
                    "fully depend",
                    "do whatever",
                    "decide for me",
                    "完全依赖",
                    "替我决定",
                    "交给你",
                    "不再复核",
                ]
            ):
                patterns.append("over_dependency")
            if any(
                token in text
                for token in ["without asking", "skip approval", "不用提醒", "无需确认"]
            ):
                patterns.append("approval_bypass_pressure")
            erosion = bool(patterns)
            return json.dumps({
                "agency_erosion_detected": erosion,
                "human_agency_preserved": not erosion,
                "approval_pressure_detected": "approval_bypass_pressure" in patterns,
                "dependency_patterns": patterns,
                "recommended_action": "needs_human_review" if erosion else "allow",
                "rationale": "Agency erosion decision from dependency and approval evidence.",
                "confidence": 0.6,
            })

        return None

    @staticmethod
    def _string_list(value) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value]
        if value is None:
            return []
        return [str(value)]

    @staticmethod
    def _as_float(value, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

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
