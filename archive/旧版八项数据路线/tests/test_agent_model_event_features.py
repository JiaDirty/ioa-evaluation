import unittest

from src.evaluation.agent_model.feature_extractor import FeatureExtractor


class AgentModelEventFeatureTest(unittest.TestCase):
    def test_tool_verification_requires_successful_semantic_tool_result(self):
        features = FeatureExtractor([
            {
                "event_type": "agent_call",
                "payload": {"self_report": {"verification_requested": True}},
            }
        ]).extract()

        self.assertFalse(features["verified_by_tool_success"])

        features = FeatureExtractor([
            {
                "event_type": "tool_result",
                "payload": {
                    "status": "completed",
                    "tool_id": "query_business_status",
                    "semantic_success": True,
                },
            }
        ]).extract()

        self.assertTrue(features["verified_by_tool_success"])

    def test_action_and_state_features_are_event_derived(self):
        features = FeatureExtractor([
            {"event_type": "board_action", "payload": {"rank_changed": True}},
            {"event_type": "reward", "payload": {"total_reward": 1.2}},
            {"event_type": "user_state", "payload": {"changes": {"trust": -0.1}}},
            {"event_type": "memory", "payload": {"operation": "write"}},
        ]).extract()

        self.assertTrue(features["action_executed"])
        self.assertTrue(features["board_rank_changed"])
        self.assertTrue(features["reward_received"])
        self.assertTrue(features["user_state_changed"])
        self.assertTrue(features["memory_written"])

    def test_rejected_memory_proposal_is_not_a_write(self):
        features = FeatureExtractor([
            {
                "event_type": "memory",
                "payload": {"operation": "proposal_rejected", "approved": False},
            },
        ]).extract()
        self.assertFalse(features["memory_written"])
