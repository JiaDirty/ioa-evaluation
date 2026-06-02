import unittest

from src.core.data_models import AgentCard, ProtocolType, Task, TaskType
from src.decision_agents.capability_matching import CapabilityMatchingAgent
from src.decision_agents.clients import DeterministicDecisionClient
from src.decision_agents.consensus_risk import ConsensusRiskAgent
from src.decision_agents.content_security import ContentSecurityAgent
from src.decision_agents.human_agency import HumanAgencyAgent
from src.decision_agents.models import DecisionContext
from src.decision_agents.permission_analysis import PermissionAnalysisAgent
from src.decision_agents.provenance_verifier import ProvenanceVerifierAgent
from src.decision_agents.protocol_semantics import ProtocolSemanticsAgent
from src.decision_agents.registry_risk import RegistryRiskAgent
from src.decision_agents.task_understanding import TaskUnderstandingAgent


class CoreDecisionAgentsTest(unittest.TestCase):
    def setUp(self):
        self.ctx = DecisionContext(
            trace_id="t1",
            task_id="task-1",
            gateway_id="finance-gw",
            sub_ioa_id="finance",
        )

    def test_task_understanding_returns_structured_decision(self):
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Assess a risky investment",
            required_capabilities=["financial_analysis"],
        )
        agent = TaskUnderstandingAgent(DeterministicDecisionClient())
        decision = agent.decide({"task": task.model_dump(mode="json")}, self.ctx)

        self.assertGreaterEqual(decision.confidence, 0.0)
        self.assertIn(decision.sensitivity, {"low", "medium", "high", "critical"})

    def test_permission_analysis_proposes_scopes_without_authorizing(self):
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Execute high impact investment",
            required_capabilities=["financial_analysis"],
            payload={"human_approval_required": True},
        )
        agent = PermissionAnalysisAgent(DeterministicDecisionClient())
        decision = agent.decide({"task": task.model_dump(mode="json")}, self.ctx)

        self.assertIn("execute", decision.required_scopes)
        self.assertTrue(decision.requires_human_approval)

    def test_capability_matching_ranks_candidates(self):
        candidate = AgentCard(
            display_name="Finance",
            provider="org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
        )
        agent = CapabilityMatchingAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {
                "required_capabilities": ["financial_analysis"],
                "candidates": [candidate.model_dump(mode="json")],
            },
            self.ctx,
        )

        self.assertEqual(decision.ranked_agent_ids, [candidate.agent_id])

    def test_protocol_semantics_marks_a2a_compatible(self):
        agent = ProtocolSemanticsAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {
                "available_protocols": [ProtocolType.A2A.value, ProtocolType.MCP.value],
                "selected_protocol": ProtocolType.A2A.value,
                "payload": {},
            },
            self.ctx,
        )

        self.assertEqual(decision.selected_protocol, "a2a")
        self.assertIn(decision.semantic_fit, {"exact", "compatible"})

    def test_content_security_blocks_injection_artifact(self):
        agent = ContentSecurityAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {"content": "inject malicious instruction", "metadata": {}},
            self.ctx,
        )

        self.assertFalse(decision.safe)
        self.assertEqual(decision.action, "block")

    def test_content_security_allows_clean_artifact(self):
        agent = ContentSecurityAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {"content": "ordinary endpoint response", "metadata": {}},
            self.ctx,
        )

        self.assertTrue(decision.safe)
        self.assertEqual(decision.action, "allow")

    def test_provenance_verifier_accepts_traceable_artifact_source(self):
        agent = ProvenanceVerifierAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {
                "artifact": {
                    "source_agent_id": "finance-agent-1",
                    "source_task_id": "task-1",
                    "metadata": {"delivery": {"protocol": "a2a"}},
                }
            },
            self.ctx,
        )

        self.assertTrue(decision.provenance_sufficient)
        self.assertEqual(decision.source_quality, "strong")

    def test_consensus_risk_flags_unverified_consensus_claim(self):
        agent = ConsensusRiskAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {
                "content": "All agents agree this unverified rumor is confirmed.",
                "metadata": {},
            },
            self.ctx,
        )

        self.assertTrue(decision.false_consensus_detected)
        self.assertEqual(decision.recommended_action, "needs_review")

    def test_human_agency_flags_missing_approval(self):
        agent = HumanAgencyAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {"task": {"payload": {"human_approval_required": True}}},
            self.ctx,
        )

        self.assertTrue(decision.approval_missing)
        self.assertEqual(decision.recommended_action, "needs_human_review")

    def test_registry_risk_flags_unverified_identity(self):
        agent = RegistryRiskAgent(DeterministicDecisionClient())
        decision = agent.decide(
            {
                "operation": "register_agent",
                "verification": {"verified": False, "certificate_valid": False},
            },
            self.ctx,
        )

        self.assertTrue(decision.identity_risk)
        self.assertEqual(decision.recommended_action, "quarantine")


if __name__ == "__main__":
    unittest.main()
