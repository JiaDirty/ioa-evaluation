import unittest

from src.core.data_models import Task, TaskType
from src.gateway.policy import AuthorizationPolicyEngine, PolicyConfig, PolicySubject


class PolicyConfigTest(unittest.TestCase):
    def test_loads_default_and_file_config(self):
        self.assertGreaterEqual(PolicyConfig.load("missing-file.yaml").max_delegation_depth, 1)
        self.assertEqual(PolicyConfig.load("config/policies.yaml").max_delegation_depth, 2)

    def test_max_delegation_depth_and_human_approval(self):
        engine = AuthorizationPolicyEngine(PolicyConfig.load("config/policies.yaml"))
        subject = PolicySubject("user", ["execute"], is_user=True)
        too_deep = Task(
            task_type=TaskType.MULTI_HOP,
            description="delegate",
            payload={"delegation_depth": 3},
        )
        self.assertFalse(engine.evaluate(subject, too_deep, ["execute"]).authorized)
        trade = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="trade",
            payload={"action_type": "trade_execution"},
        )
        self.assertFalse(engine.evaluate(subject, trade, ["execute"]).authorized)
        trade.payload["human_approval_granted"] = True
        self.assertTrue(engine.evaluate(subject, trade, ["execute"]).authorized)


if __name__ == "__main__":
    unittest.main()
