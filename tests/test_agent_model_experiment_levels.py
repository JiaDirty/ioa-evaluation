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


class _Environment:
    def __init__(self):
        self.tasks = []

    async def submit_task(self, task):
        self.tasks.append(task)
        output = {
            "type": "final",
            "business_output": {"answer": "controlled"},
            "behavior_record": {"claims": [{
                "claim_id": "说法-01",
                "statement": "受控共识命题",
                "disposition": "uncertain",
            }]},
        }
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id=task.payload["role_id"],
            content=output,
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=output,
            artifacts=[artifact],
            participating_agents=[task.payload["role_id"]],
        )


class AgentModelExperimentLevelsTest(unittest.IsolatedAsyncioTestCase):
    async def test_consensus_key_node_and_ecosystem_have_distinct_topologies(self):
        case = CaseLoader(DATASET).load_all()["CON-01"]
        key_env = _Environment()
        key_runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=key_env, fake_model=True,
            experiment_level="key_node",
        )
        await key_runner.open()
        try:
            key_result = await key_runner._run_variant(case, "risk", "key-risk", 0)
        finally:
            await key_runner.close()

        eco_env = _Environment()
        eco_runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=eco_env, fake_model=True,
            experiment_level="ecosystem",
        )
        await eco_runner.open()
        try:
            eco_result = await eco_runner._run_variant(case, "risk", "eco-risk", 0)
            artifact_events = eco_runner._context_store.list_events(
                "eco-risk", "artifact"
            )
        finally:
            await eco_runner.close()

        self.assertEqual(key_result.experiment_level, "key_node")
        self.assertEqual(eco_result.experiment_level, "ecosystem")
        self.assertEqual(len(key_env.tasks), 1)
        self.assertEqual(len(eco_env.tasks), len(case.scenario.reports) + 1)
        self.assertEqual(
            len(eco_result.system_response["ecosystem_report_artifact_ids"]),
            len(case.scenario.reports),
        )
        synthesis_event = artifact_events[-1]
        self.assertEqual(
            len(synthesis_event["payload"]["parent_artifact_ids"]),
            len(case.scenario.reports),
        )
