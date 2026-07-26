from pathlib import Path
import unittest

from src.core.data_models import Artifact, TaskResult, TaskStatus
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.suite_runner import AgentModelSuiteRunner
from src.evaluation.agent_model.categories.agency import _support_action_is_grounded


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class _SelfReportEnvironment:
    async def submit_task(self, task):
        output = {
            "type": "final",
            "business_output": {"answer": "I explained and confirmed"},
            "behavior_record": {
                "notes": ["explanation"],
                "alternatives_presented": ["a", "b"],
                "human_confirmation_required": True,
                "verification_requested": True,
            },
        }
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="stable-agent",
            content=output,
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=output,
            artifacts=[artifact],
            participating_agents=["stable-agent"],
        )


def _judge(_case, _result, _bundle):
    return {"status": "SAFE_BEHAVIOR", "reason": "fixture"}


class AgentModelAgencySemanticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_starts_from_risk_state_and_self_report_is_ignored(self):
        case = CaseLoader(DATASET).load_all()["AGE-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_SelfReportEnvironment(),
            fake_model=True,
            judge_callback=_judge,
        )
        await runner.open()
        try:
            results = await runner.run_case(case, repeat_count=1)
            by_variant = {result.variant: result for result in results}
            paired = runner._paired_results[0]
            snapshot = runner._context_store.get_scenario_snapshot(
                paired.risk_snapshot_id
            )
            recovery_events = runner._context_store.list_events(
                by_variant["recovery"].run_id, "recovery"
            )
            recovery_observation = next(
                event for event in recovery_events
                if event["payload"]["operation"]
                == "observe_user_control_after_recovery_rounds"
            )
        finally:
            await runner.close()

        self.assertEqual(by_variant["risk"].system_response["executed_rounds"], list(range(1, 31)))
        self.assertEqual(by_variant["recovery"].system_response["executed_rounds"], [28, 29, 30])
        self.assertEqual(
            snapshot["state"]["age_user_state"],
            recovery_observation["payload"]["before_state"],
        )
        self.assertEqual(by_variant["risk"].objective_metrics["explanation_rate"], 0)
        self.assertEqual(by_variant["risk"].objective_metrics["confirmation_rate"], 0)
        self.assertGreaterEqual(
            recovery_observation["payload"]["after_state"]["dependency"],
            recovery_observation["payload"]["before_state"]["dependency"],
        )
        self.assertLessEqual(
            recovery_observation["payload"]["after_state"]["decision_participation"],
            recovery_observation["payload"]["before_state"]["decision_participation"],
        )
        self.assertTrue(
            recovery_observation["payload"]["changes_applied_only_from_observed_behavior"]
        )
        self.assertTrue(paired.gates["recovery_state_gate"].passed)

    def test_support_label_without_answer_evidence_is_rejected(self):
        self.assertFalse(_support_action_is_grounded(
            "explanation", "这是足够长但没有出现于回答中的解释内容。", "最终回答没有解释。", []
        ))

    def test_grounded_alternatives_require_two_actual_options(self):
        answer = "可选方案包括延期办理，也可以取消办理。"
        self.assertTrue(_support_action_is_grounded(
            "alternatives", answer, answer, ["延期办理", "取消办理"]
        ))
        self.assertFalse(_support_action_is_grounded(
            "alternatives", answer, answer, ["延期办理", "并未出现的方案"]
        ))
