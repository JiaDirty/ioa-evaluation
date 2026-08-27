import unittest

from src.core.data_models import Task, TaskType
from src.gateway.policy import AuthorizationPolicyEngine, PolicyConfig, PolicySubject


class AgentDelegationPolicyTest(unittest.TestCase):
    def test_delegation_depth_is_enforced_from_policy_config(self):
        engine = AuthorizationPolicyEngine(PolicyConfig.load("config/policies.yaml"))
        subject = PolicySubject("agent", ["execute"], sub_ioa_id="finance", reputation_score=1.0)
        task = Task(
            task_type=TaskType.MULTI_HOP,
            description="delegate",
            payload={"delegation_depth": 99},
        )
        decision = engine.evaluate(subject, task, ["execute"])
        self.assertFalse(decision.authorized)
        self.assertIn("delegation_depth", decision.reason)


if __name__ == "__main__":
    unittest.main()
