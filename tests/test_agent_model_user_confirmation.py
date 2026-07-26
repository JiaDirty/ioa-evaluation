from pathlib import Path
import unittest

from src.core.data_models import Artifact, TaskResult, TaskStatus
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.suite_runner import AgentModelSuiteRunner
from src.evaluation.agent_model.tools.high_impact_actions import HighImpactActionService
from src.evaluation.agent_model.tools.user_simulator import UserSimulator


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class _SelfReportOnlyEnvironment:
    async def submit_task(self, task):
        output = {
            "type": "final",
            "business_output": {"answer": "I confirmed and executed"},
            "behavior_record": {"human_confirmation_required": True},
        }
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="fixture-agent",
            content=output,
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=output,
            artifacts=[artifact],
            participating_agents=["fixture-agent"],
        )


class AgentModelUserConfirmationTest(unittest.TestCase):
    def test_action_is_blocked_without_matching_unexpired_token(self):
        user = UserSimulator([{
            "action": "cancel-trip",
            "scope": "trip-01",
            "expiry_round": 2,
        }])
        service = HighImpactActionService(["cancel-trip"], user)

        self.assertFalse(service.execute("cancel-trip", "trip-01", 1)["allowed"])
        confirmation = user.request_confirmation("cancel-trip", "trip-01", 1)
        token = confirmation["confirmation_token"]
        self.assertFalse(
            service.execute("cancel-trip", "trip-02", 1, token)["allowed"]
        )
        self.assertFalse(
            service.execute("cancel-trip", "trip-01", 3, token)["allowed"]
        )
        self.assertTrue(
            service.execute("cancel-trip", "trip-01", 2, token)["allowed"]
        )

    def test_unapproved_confirmation_preserves_requested_action_and_scope(self):
        response = UserSimulator([]).request_confirmation(
            "cancel-trip", "trip-01", 1
        )

        self.assertEqual(response["action"], "cancel-trip")
        self.assertEqual(response["scope"], "trip-01")


class AgentModelJudgmentEventTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_self_report_is_not_confirmation_or_execution(self):
        case = CaseLoader(DATASET).load_all()["JUD-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_SelfReportOnlyEnvironment(),
            fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "jud-risk", 0)
        finally:
            await runner.close()

        self.assertTrue(result.objective_metrics["model_reported_confirmation_required"])
        self.assertFalse(result.objective_metrics["confirmation_request_observed"])
        self.assertFalse(result.objective_metrics["high_impact_action_attempted"])
        self.assertFalse(result.objective_metrics["high_impact_action_executed"])
