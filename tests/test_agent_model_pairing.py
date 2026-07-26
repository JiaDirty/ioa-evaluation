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
        public_state = task.payload.get("public_state", {})
        visible = public_state.get("active_information", {})
        required = public_state.get("required_claim_assessment", {})
        if visible:
            self.active_claim = {
                "claim_id": visible["claim_id"],
                "statement": visible["statement"],
            }
        elif required and self.active_claim:
            self.active_claim["claim_id"] = required["claim_id"]
        output = {
            "type": "final",
            "business_output": {
                "answer": "controlled fixture answer",
                "decision": "complete",
                "confidence": 0.8,
                "limitations": [],
            },
            "behavior_record": {"claims": ([{
                "claim_id": self.active_claim["claim_id"],
                "statement": self.active_claim["statement"],
                "disposition": "rejected",
                "forwarded": False,
                "used_for_action": False,
            }] if self.active_claim else [])},
            "reason": "",
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


class _VariantBindingEnvironment(_ProtocolEnvironment):
    async def submit_task(self, task):
        result = await super().submit_task(task)
        agent = "risk-agent" if task.payload["variant"] == "risk" else "stable-agent"
        return result.model_copy(update={"participating_agents": [agent]})


class _RecoveryCorrectionEnvironment(_ProtocolEnvironment):
    def __init__(self):
        super().__init__()
        self.corrects_claim_id = ""

    async def submit_task(self, task):
        result = await super().submit_task(task)
        variant = task.payload["variant"]
        active = task.payload.get("public_state", {}).get(
            "active_information", {}
        )
        if active.get("related_claim_id"):
            self.corrects_claim_id = active["related_claim_id"]
        claim = result.output["behavior_record"]["claims"][0]
        if variant in {"risk", "recovery"}:
            claim.update({
                "disposition": "accepted",
                "forwarded": True,
                "used_for_action": True,
            })
        if variant == "recovery":
            claim["corrects_claim_id"] = self.corrects_claim_id
        artifact = result.artifacts[0].model_copy(
            update={"content": result.output}
        )
        return result.model_copy(
            update={"output": result.output, "artifacts": [artifact]}
        )


def _judge(_case, _result, _bundle):
    return {"status": "SAFE_BEHAVIOR", "reason": "controlled fixture verdict"}


class AgentModelPairingTest(unittest.IsolatedAsyncioTestCase):
    async def test_pair_uses_independent_baseline_and_shared_risk_recovery_state(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_ProtocolEnvironment(),
            fake_model=True,
            judge_callback=_judge,
            suite_run_id="pair-test",
        )
        await runner.open()
        try:
            results = await runner.run_case(case, repeat_count=1)
            paired = runner._paired_results[0]
            snapshot = runner._context_store.get_scenario_snapshot(
                paired.risk_snapshot_id
            )
        finally:
            await runner.close()

        by_variant = {result.variant: result for result in results}
        self.assertNotEqual(
            by_variant["baseline"].scenario_state_id,
            by_variant["risk"].scenario_state_id,
        )
        self.assertEqual(
            by_variant["risk"].scenario_state_id,
            by_variant["recovery"].scenario_state_id,
        )
        self.assertEqual(
            by_variant["recovery"].parent_snapshot_id,
            paired.risk_snapshot_id,
        )
        self.assertEqual(snapshot["source_run_id"], by_variant["risk"].run_id)

    async def test_jud_recovery_uses_controlled_user_state_change(self):
        case = CaseLoader(DATASET).load_all()["JUD-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_ProtocolEnvironment(),
            fake_model=True,
            judge_callback=_judge,
        )
        await runner.open()
        try:
            await runner.run_case(case, repeat_count=1)
            paired = runner._paired_results[0]
        finally:
            await runner.close()

        self.assertTrue(paired.gates["baseline_gate"].passed)
        self.assertTrue(paired.gates["judge_gate"].passed)
        self.assertTrue(paired.gates["recovery_state_gate"].passed)

    async def test_cascade_recovery_invalidates_risk_artifacts(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_RecoveryCorrectionEnvironment(),
            fake_model=True,
            judge_callback=_judge,
        )
        await runner.open()
        try:
            await runner.run_case(case, repeat_count=1)
            paired = runner._paired_results[0]
            recovery = runner._context_store.list_events(
                paired.recovery_run_id, "recovery"
            )
        finally:
            await runner.close()

        self.assertTrue(paired.gates["recovery_state_gate"].passed)
        lifecycle_event = next(
            event for event in recovery
            if event["payload"].get("operation")
            == "invalidate_and_correct_artifacts"
        )
        self.assertTrue(lifecycle_event["payload"]["affected_event_ids"])
        self.assertTrue(
            lifecycle_event["payload"]["after_state"]["invalidated_artifact_ids"]
        )

    async def test_invalid_baseline_excludes_entire_pair(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]

        def selective_judge(_case, result, _bundle):
            if result.variant == "baseline":
                return {"status": "INVALID", "reason": "baseline task failed"}
            return {"status": "SAFE_BEHAVIOR", "reason": "fixture"}

        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_ProtocolEnvironment(),
            fake_model=True,
            judge_callback=selective_judge,
        )
        await runner.open()
        try:
            await runner.run_case(case, repeat_count=1)
            paired = runner._paired_results[0]
        finally:
            await runner.close()

        self.assertFalse(paired.gates["baseline_gate"].passed)
        self.assertFalse(paired.formal_aggregate_eligible)

    async def test_role_binding_change_fails_pair(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_VariantBindingEnvironment(),
            fake_model=True,
            judge_callback=_judge,
        )
        await runner.open()
        try:
            await runner.run_case(case, repeat_count=1)
            paired = runner._paired_results[0]
        finally:
            await runner.close()

        self.assertFalse(paired.gates["binding_and_config_gate"].passed)
        self.assertFalse(paired.formal_aggregate_eligible)
