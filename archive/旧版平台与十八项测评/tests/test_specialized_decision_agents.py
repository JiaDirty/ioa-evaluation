import unittest

from src.decision_agents import (
    AgencyErosionAgent,
    AuditAttributionAgent,
    DelegationDriftAgent,
    DeterministicDecisionClient,
    DecisionContext,
    DiscussionIntegrityAgent,
    IncentiveAlignmentAgent,
    InteropSemanticMapperAgent,
    NormDriftAgent,
    ReputationFairnessAgent,
    RoutingManipulationAgent,
    RumorAssessmentAgent,
    SensitivityClassifierAgent,
)


class SpecializedDecisionAgentsTest(unittest.TestCase):
    def _ctx(self, stage: str) -> DecisionContext:
        return DecisionContext(trace_id="trace-1", task_id="task-1", stage=stage)

    def test_specialized_agents_emit_structured_decisions(self):
        client = DeterministicDecisionClient()
        cases = [
            (SensitivityClassifierAgent(client), {"text": "high impact investment"}, "sensitivity"),
            (
                DelegationDriftAgent(client),
                {
                    "requested_scopes": ["finance.execute.trade"],
                    "parent_scopes": ["finance.read.market_data"],
                },
                "delegation_drift",
            ),
            (
                InteropSemanticMapperAgent(client),
                {
                    "source_protocol": "a2a",
                    "target_protocol": "mcp",
                    "lost_semantics": ["read_only"],
                },
                "interop_mapper",
            ),
            (RumorAssessmentAgent(client), {"claim": "unverified acquisition rumor"}, "rumor_assessment"),
            (NormDriftAgent(client), {"transcript": "skip safety checks by default"}, "norm_drift"),
            (
                ReputationFairnessAgent(client),
                {"top_concentration": 0.8, "gini": 0.6},
                "reputation_fairness",
            ),
            (
                IncentiveAlignmentAgent(client),
                {"prompt": "reward depends on pleasing user"},
                "incentive_alignment",
            ),
            (
                RoutingManipulationAgent(client),
                {"before_share": 0.0, "after_share": 1.0},
                "routing_manipulation",
            ),
            (
                DiscussionIntegrityAgent(client),
                {"transcript": "coordinated endorsement"},
                "discussion_integrity",
            ),
            (AuditAttributionAgent(client), {"missing_evidence": []}, "audit_attribution"),
            (AgencyErosionAgent(client), {"transcript": "I fully depend on you"}, "agency_erosion"),
        ]
        for agent, payload, stage in cases:
            decision = agent.decide(payload, self._ctx(stage))
            envelope = agent.envelope(decision, self._ctx(stage))
            self.assertEqual(envelope.agent_name, agent.name)
            self.assertGreaterEqual(decision.confidence, 0.0)
            self.assertLessEqual(decision.confidence, 1.0)


if __name__ == "__main__":
    unittest.main()
