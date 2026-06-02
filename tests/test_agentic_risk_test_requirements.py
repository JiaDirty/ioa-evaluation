import unittest

from risk_tests.base_test import BaseIoARiskTest
from risk_tests.registry import ALL_TESTS
from risk_tests.realism import get_realism_profile
from src.core.data_models import Artifact, RiskLevel, TaskResult, TaskStatus, TestResult
from src.experiment.exceptions import EvaluationInvalidError


class _ConcreteRiskTest(BaseIoARiskTest):
    test_id = "ioa_identity_spoofing"
    test_name = "concrete"
    category = "trust_authorization"

    async def run(self, env, **kwargs) -> TestResult:
        return self.make_result(passed=True, risk_level=RiskLevel.LOW)


class AgenticRiskTestRequirementsTest(unittest.TestCase):
    def test_all_registered_tests_declare_agentic_gateway_requirements(self):
        for test in ALL_TESTS:
            profile = get_realism_profile(test.test_id)
            chain = [str(item).lower() for item in profile.get("communication_chain", [])]
            components = [str(item).lower() for item in profile.get("infrastructure_components", [])]
            combined = chain + components

            self.assertTrue(profile.get("required_decision_agents"), test.test_id)
            self.assertTrue(profile.get("agent_in_loop"), test.test_id)
            self.assertTrue(any("gateway" in item for item in combined), test.test_id)

    def test_require_decision_evidence_rejects_missing_required_agents(self):
        test = _ConcreteRiskTest()
        result = TaskResult(
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            artifacts=[Artifact(content="ok", metadata={"decision_agents": {}})],
        )

        with self.assertRaises(EvaluationInvalidError):
            test.require_decision_evidence(
                result,
                ["TaskUnderstandingAgent"],
                "missing evidence",
            )

    def test_require_task_completed_accepts_full_decision_evidence(self):
        test = _ConcreteRiskTest()
        required = get_realism_profile(test.test_id)["required_decision_agents"]
        decisions = {
            agent_name: {"agent_name": agent_name}
            for agent_name in required
        }
        result = TaskResult(
            task_id="task-1",
            status=TaskStatus.COMPLETED,
            artifacts=[Artifact(content="ok", metadata={"decision_agents": decisions})],
        )

        test.require_task_completed(result, "complete evidence")


if __name__ == "__main__":
    unittest.main()
