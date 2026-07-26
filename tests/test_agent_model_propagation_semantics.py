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
        self.active_claim = {}

    async def submit_task(self, task):
        self.tasks.append(task)
        visible = task.payload.get("public_state", {}).get("active_information", {})
        if visible:
            self.active_claim = visible
        output = {
            "type": "final",
            "business_output": {"answer": "claimed forwarding"},
            "behavior_record": {
                "claims": [{
                    "claim_id": self.active_claim.get("claim_id", ""),
                    "statement": self.active_claim.get("statement", ""),
                    "disposition": "accepted",
                    "forwarded": True,
                }],
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


class _RejectingEnvironment(_ProtocolEnvironment):
    async def submit_task(self, task):
        result = await super().submit_task(task)
        output = result.output
        output["behavior_record"]["claims"][0].update({
            "disposition": "rejected",
            "forwarded": False,
        })
        artifact = result.artifacts[0].model_copy(update={"content": output})
        return result.model_copy(update={"output": output, "artifacts": [artifact]})


class _ImpactingEnvironment(_ProtocolEnvironment):
    async def submit_task(self, task):
        result = await super().submit_task(task)
        output = result.output
        output["behavior_record"]["claims"][0]["used_for_action"] = True
        artifact = result.artifacts[0].model_copy(update={"content": output})
        return result.model_copy(update={"output": output, "artifacts": [artifact]})


class AgentModelPropagationSemanticsTest(unittest.IsolatedAsyncioTestCase):
    async def test_rejected_claim_is_not_delivered_to_downstream_roles(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _RejectingEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=environment, fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "cas-reject", 0)
            forwards = runner._context_store.list_events(
                "cas-reject", "message_forward"
            )
        finally:
            await runner.close()

        self.assertEqual(result.objective_metrics["real_propagation_depth"], 0)
        self.assertEqual(forwards, [])
        self.assertEqual(len(environment.tasks), 1)

    async def test_cascade_depth_comes_from_received_artifact_edges(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _ImpactingEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=environment, fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "cas-risk", 0)
            forwards = runner._context_store.list_events(
                "cas-risk", "message_forward"
            )
        finally:
            await runner.close()

        expected_edges = len(case.scenario.agent_chain) - 1
        self.assertEqual(result.objective_metrics["real_propagation_depth"], expected_edges)
        self.assertEqual(
            result.objective_metrics["active_information_forward_depth"],
            expected_edges,
        )
        self.assertEqual(len(forwards), expected_edges)
        self.assertIn("active_information", environment.tasks[0].payload["public_state"])
        self.assertTrue(all(
            "forwarded 必须明确记录 true 或 false" in task.task_spec.capability_requirements[0].expected_output
            for task in environment.tasks
        ))
        self.assertTrue(all(
            "active_information" not in task.payload["public_state"]
            for task in environment.tasks[1:]
        ))
        self.assertEqual(
            result.objective_metrics["false_claim_acceptance_rate"], 1.0
        )

    async def test_forwarding_without_business_impact_is_not_cascade_impact(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=_ProtocolEnvironment(), fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "cas-forward-only", 0)
        finally:
            await runner.close()

        self.assertEqual(result.objective_metrics["real_propagation_depth"], 0)
        self.assertEqual(
            result.objective_metrics["active_information_forward_depth"],
            len(case.scenario.agent_chain) - 1,
        )

    async def test_rumor_depth_is_not_taken_from_model_self_report(self):
        case = CaseLoader(DATASET).load_all()["RUM-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=_ProtocolEnvironment(), fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "rum-risk", 0)
        finally:
            await runner.close()

        self.assertEqual(
            result.objective_metrics["real_forward_count"],
            len(case.scenario.propagation_chain) - 1,
        )
        self.assertEqual(
            result.objective_metrics["source_label_retention_rate"], 0.0
        )
