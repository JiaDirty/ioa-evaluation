import unittest

from risk_tests.base_test import BaseIoARiskTest
from src.core.data_models import TaskResult, TaskStatus
from src.experiment.exceptions import EvaluationInvalidError


class _DummyRiskTest(BaseIoARiskTest):
    test_id = "dummy"
    test_name = "Dummy"
    category = "dummy"

    async def run(self, env, **kwargs):
        raise NotImplementedError


class RiskTestValidityGuardsTest(unittest.TestCase):
    def test_failed_task_result_is_invalid_evaluation_evidence(self):
        risk_test = _DummyRiskTest()
        result = TaskResult(
            task_id="task-1",
            status=TaskStatus.FAILED,
            error="Agent belongs to finance, not healthcare",
        )

        with self.assertRaises(EvaluationInvalidError):
            risk_test.require_task_completed(result, "cascade propagation range")

    def test_completed_task_result_can_be_used_as_evidence(self):
        risk_test = _DummyRiskTest()
        result = TaskResult(task_id="task-1", status=TaskStatus.COMPLETED)

        risk_test.require_task_completed(result, "cascade propagation range")


if __name__ == "__main__":
    unittest.main()
