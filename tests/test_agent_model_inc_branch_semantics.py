from pathlib import Path
import unittest

from src.core.data_models import Artifact, TaskResult, TaskStatus
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.suite_runner import AgentModelSuiteRunner


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class _ProtocolEnvironment:
    def __init__(self):
        self.tasks = []

    async def submit_task(self, task):
        self.tasks.append(task)
        round_index = task.payload.get("public_state", {}).get("round")
        output = {
            "type": "final",
            "business_output": {"answer": f"round-{round_index}"},
            "behavior_record": {},
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


class AgentModelIncBranchSemanticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_risk_stops_at_25_and_recovery_runs_only_26_to_30(self):
        case = CaseLoader(DATASET).load_all()["INC-01"]
        environment = _ProtocolEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=environment,
            fake_model=True,
            judge_callback=_judge,
        )
        await runner.open()
        try:
            results = await runner.run_case(case, repeat_count=1)
            by_variant = {result.variant: result for result in results}
            risk_events = runner._context_store.list_events(
                by_variant["risk"].run_id, "agent_call"
            )
            recovery_events = runner._context_store.list_events(
                by_variant["recovery"].run_id, "agent_call"
            )
            recovery_state = runner._context_store.get_run_state(
                by_variant["recovery"].run_id
            )
            control = by_variant["recovery"].system_response[
                "continued_misaligned_control"
            ]
            control_events = runner._context_store.list_events(
                control["run_id"], "agent_call"
            )
            control_state = runner._context_store.get_run_state(
                control["run_id"]
            )
            paired = runner._paired_results[0]
        finally:
            await runner.close()

        self.assertEqual(by_variant["risk"].system_response["executed_rounds"], list(range(1, 26)))
        self.assertEqual(by_variant["recovery"].system_response["executed_rounds"], list(range(26, 31)))
        self.assertEqual(len(risk_events), 25)
        self.assertEqual(len(recovery_events), 5)
        self.assertEqual(len(control_events), 5)
        self.assertEqual(
            control["parent_snapshot_id"], paired.risk_snapshot_id
        )
        self.assertEqual(recovery_state["inc_last_round"], 30)
        self.assertEqual(control_state["inc_last_round"], 30)
        self.assertEqual(control_state["status"], "completed")
        self.assertEqual(control_state["result_status"], "CONTROL_COMPLETED")
        self.assertEqual(len(recovery_state["inc_behavior_log"]), 30)
        self.assertEqual(recovery_state["inc_behavior_log"][24]["round"], 25)
        self.assertEqual(recovery_state["inc_behavior_log"][25]["round"], 26)
        self.assertIn(
            "continued_misaligned_control",
            by_variant["recovery"].objective_metrics,
        )
        self.assertIn(
            "recovery_vs_continued_misaligned",
            by_variant["recovery"].objective_metrics,
        )
        round_27_recovery = next(
            task for task in environment.tasks
            if task.root_task_id == by_variant["recovery"].run_id
            and task.payload["public_state"]["round"] == 27
        )
        self.assertIn(
            "round-26",
            str(round_27_recovery.payload["turn_history"]),
        )
        self.assertTrue(paired.gates["recovery_state_gate"].passed)
        self.assertTrue(paired.gates["binding_and_config_gate"].passed)
