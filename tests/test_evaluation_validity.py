import unittest

from risk_tests.base_test import BaseIoARiskTest
from src.core.data_models import EvaluationStatus
from src.experiment.runner import ExperimentRunner, IoAEnvironment
from src.experiment.exceptions import EvaluationInvalidError


class _DummyRiskTest(BaseIoARiskTest):
    test_id = "dummy"
    test_name = "Dummy"
    category = "unit"

    async def run(self, env, **kwargs):
        raise NotImplementedError


class _FailingAgentEnv:
    def run_agent_task(self, sub_ioa_id, task):
        raise RuntimeError("agent backend unavailable")


class _FailingJudge:
    def judge_attack_success(self, goal, response):
        raise RuntimeError("judge backend unavailable")


class _FailingJudgeEnv:
    def get_judge(self, risk_type):
        return _FailingJudge()


class EvaluationValidityTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_backend_failure_marks_evaluation_invalid(self):
        test = _DummyRiskTest()

        with self.assertRaises(EvaluationInvalidError):
            await test.run_agent_task(_FailingAgentEnv(), "finance", "task")

    async def test_judge_backend_failure_marks_evaluation_invalid(self):
        test = _DummyRiskTest()

        with self.assertRaises(EvaluationInvalidError):
            await test.judge_attack(
                _FailingJudgeEnv(),
                "identity_spoofing",
                "attack goal",
                "target response",
            )

    async def test_runner_reports_invalid_evaluation_separately(self):
        async def invalid_test(env):
            raise EvaluationInvalidError("judge unavailable")

        runner = ExperimentRunner(IoAEnvironment({"create_agent_runtimes": False}))

        result = await runner.run_single_test("invalid_case", invalid_test)
        report = await runner.generate_report()

        self.assertEqual(result.status, EvaluationStatus.INVALID)
        self.assertFalse(result.passed)
        self.assertEqual(report["summary"]["total_tests"], 1)
        self.assertEqual(report["summary"]["valid_tests"], 0)
        self.assertEqual(report["summary"]["invalid_tests"], 1)
        self.assertEqual(report["summary"]["valid_pass_rate"], 0.0)

    async def test_runner_records_unexpected_test_exception_as_invalid(self):
        async def broken_test(env):
            raise RuntimeError("test implementation bug")

        runner = ExperimentRunner(IoAEnvironment({"create_agent_runtimes": False}))

        result = await runner.run_single_test("broken_case", broken_test)

        self.assertEqual(result.status, EvaluationStatus.INVALID)
        self.assertFalse(result.passed)
        self.assertIn("test implementation bug", result.explanation)


if __name__ == "__main__":
    unittest.main()
