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


class _NoMemoryActionEnvironment:
    async def submit_task(self, task):
        output = {
            "status": "COMPLETED",
            "decision": "Use the formal rule and write this exception to memory.",
            "answer": "use formal rule",
            "evidence_refs": ["说明-01"],
            "next_action": "Continue the workflow.",
            "handoff_message": "",
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


def _judge(_case, _result, _bundle):
    return {"status": "SAFE_BEHAVIOR", "reason": "fixture"}


class AgentModelNormMemoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_model_self_report_does_not_create_memory_metric(self):
        case = CaseLoader(DATASET).load_all()["NOR-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_NoMemoryActionEnvironment(),
            fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "nor-risk", 0)
        finally:
            await runner.close()

        self.assertEqual(result.objective_metrics["approved_memory_write_count"], 0)
        self.assertEqual(result.objective_metrics["memory_write_rate"], 0)
        self.assertIsNone(result.objective_metrics["expired_exception_reuse_rate"])
        self.assertFalse(result.objective_metrics["event_features"]["memory_written"])

    async def test_risk_and_recovery_share_rule_state_without_repeating_rounds(self):
        case = CaseLoader(DATASET).load_all()["NOR-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_NoMemoryActionEnvironment(),
            fake_model=True,
            judge_callback=_judge,
        )
        await runner.open()
        try:
            results = await runner.run_case(case, repeat_count=1)
            by_variant = {result.variant: result for result in results}
            risk_sessions = runner._context_store.conn.execute(
                "SELECT DISTINCT role_id FROM agent_sessions WHERE run_id=?",
                (by_variant["risk"].run_id,),
            ).fetchall()
            paired = runner._paired_results[0]
        finally:
            await runner.close()

        self.assertEqual(by_variant["risk"].system_response["executed_rounds"], list(range(1, 9)))
        self.assertEqual(by_variant["recovery"].system_response["executed_rounds"], [9, 10, 11])
        self.assertEqual(
            {row["role_id"] for row in risk_sessions},
            {"primary_domain_agent", "secondary_domain_agent", "new_domain_agent"},
        )
        self.assertTrue(by_variant["recovery"].system_response["shared_memory_inherited"])
        self.assertTrue(paired.gates["recovery_state_gate"].passed)
        self.assertTrue(paired.gates["binding_and_config_gate"].passed)
