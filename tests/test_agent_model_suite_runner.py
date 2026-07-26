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


class _FakeEnvironment:
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
        claims = []
        if self.active_claim:
            claims = [{
                "claim_id": self.active_claim["claim_id"],
                "statement": self.active_claim["statement"],
                "disposition": "accepted",
                "forwarded": True,
                "used_for_action": True,
            }]
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="fake-agent",
            content={
                "business_output": {
                    "answer": task.description,
                    "decision": "complete",
                    "confidence": 0.8,
                    "limitations": [],
                },
                "behavior_record": {
                    "claims": claims,
                    "verification_requested": True,
                },
            },
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=artifact.content,
            artifacts=[artifact],
            participating_agents=["fake-agent"],
        )


class _PlainTextEnvironment(_FakeEnvironment):
    async def submit_task(self, task):
        self.tasks.append(task)
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="plain-agent",
            content={"text": "plain answer"},
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=artifact.content,
            artifacts=[artifact],
            participating_agents=["plain-agent"],
        )


class _TracingEnvironment(_FakeEnvironment):
    async def submit_task(self, task):
        result = await super().submit_task(task)
        trace = {
            "request": {"messages": [{"role": "user", "content": task.description}]},
            "response": {"raw": result.output, "parsed": result.output},
        }
        artifact = result.artifacts[0].model_copy(update={
            "metadata": {"model_call_traces": [trace]},
        })
        return result.model_copy(update={
            "artifacts": [artifact],
            "metadata": {"model_call_traces": [trace]},
        })


class AgentModelSuiteRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_risk_execution_does_not_run_recovery(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _PlainTextEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=environment, fake_model=True,
        )
        await runner.open()
        try:
            results = await runner.run_case(
                case, variants=["risk", "recovery"], repeat_count=1
            )
        finally:
            await runner.close()

        by_variant = {result.variant: result for result in results}
        self.assertEqual(
            by_variant["risk"].judge_verdict["status"],
            "INVALID_EVIDENCE_INCOMPLETE",
        )
        self.assertEqual(
            by_variant["recovery"].judge_verdict["status"],
            "INVALID_RISK_PRECONDITION",
        )
        self.assertFalse(any(
            task.payload["variant"] == "recovery" for task in environment.tasks
        ))

    async def test_recovery_replays_matching_risk_role_history(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _FakeEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=environment,
            fake_model=True,
        )
        await runner.open()
        try:
            await runner.run_case(
                case,
                variants=["risk", "recovery"],
                repeat_count=1,
            )
        finally:
            await runner.close()

        recovery_tasks = [
            task
            for task in environment.tasks
            if task.payload["variant"] == "recovery"
        ]
        self.assertTrue(recovery_tasks)
        self.assertTrue(recovery_tasks[0].payload["turn_history"])
        self.assertIn(
            "business_output",
            str(recovery_tasks[0].payload["turn_history"][0]["output_json"]),
        )

    async def test_all_eight_category_runners_execute_with_fake_environment(self):
        cases = CaseLoader(DATASET).load_all()
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_FakeEnvironment(),
            fake_model=True,
        )
        await runner.open()
        try:
            results = []
            for case_id in [
                "CAS-01",
                "CON-01",
                "RUM-01",
                "NOR-01",
                "INC-01",
                "JUD-01",
                "DIS-01",
                "AGE-01",
            ]:
                results.append(
                    await runner._run_variant(
                        cases[case_id], "risk", f"run-{case_id}", 0
                    )
                )
        finally:
            await runner.close()

        self.assertEqual(len(results), 8)
        self.assertTrue(all(result.status == "INVALID" for result in results))
        self.assertTrue(
            all(result.judge_verdict["status"] in {
                "UNJUDGED", "INVALID_EVIDENCE_CONTRADICTION"
            } for result in results)
        )

    async def test_category_runner_executes_but_stays_invalid_without_judge(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_FakeEnvironment(),
            fake_model=True,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "run-test", 0)
        finally:
            await runner.close()

        self.assertEqual(result.status, "INVALID")
        self.assertEqual(result.judge_verdict["status"], "UNJUDGED")
        self.assertEqual(result.system_response["chain_executed"], 5)
        self.assertIn("event_features", result.objective_metrics)
        self.assertGreater(result.objective_metrics["event_features"]["artifact_count"], 0)

    async def test_judge_callback_controls_final_status(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]

        def judge(_case, _result, bundle):
            self.assertGreater(bundle["evidence_count"], 0)
            return {"status": "RISK_PROPAGATED", "reason": "fixture verdict"}

        runner = AgentModelSuiteRunner(
            db_path=":memory:",
            environment=_FakeEnvironment(),
            fake_model=True,
            judge_callback=judge,
        )
        await runner.open()
        try:
            result = await runner._run_variant(case, "risk", "run-test", 0)
        finally:
            await runner.close()

        self.assertEqual(result.status, "RISK_PROPAGATED")
        self.assertEqual(result.judge_verdict["reason"], "fixture verdict")

    async def test_judge_bundle_contains_raw_outputs_and_artifact_lineage(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        captured = {}

        def judge(_case, _result, bundle):
            captured.update(bundle)
            return {"status": "SAFE_BEHAVIOR", "reason": "fixture"}

        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=_FakeEnvironment(),
            fake_model=True, judge_callback=judge,
        )
        await runner.open()
        try:
            await runner._run_variant(case, "risk", "run-evidence", 0)
        finally:
            await runner.close()

        agent_calls = [item for item in captured["evidence"] if item["type"] == "agent_call"]
        self.assertTrue(agent_calls)
        self.assertIn("behavior_record", agent_calls[0]["raw_output"])
        self.assertTrue(agent_calls[0]["artifact_ids"])
        self.assertIn("public_state", agent_calls[0]["raw_input"])

    async def test_judge_bundle_contains_each_model_call_response(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        captured = {}

        def judge(_case, _result, bundle):
            captured.update(bundle)
            return {"status": "SAFE_BEHAVIOR", "reason": "fixture"}

        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=_TracingEnvironment(),
            fake_model=True, judge_callback=judge,
        )
        await runner.open()
        try:
            await runner._run_variant(case, "risk", "run-model-call-evidence", 0)
        finally:
            await runner.close()

        model_calls = [
            item for item in captured["evidence"]
            if item["type"] == "runtime_event"
            and item["event_type"] == "model_call"
        ]
        self.assertTrue(model_calls)
        self.assertIn("response", model_calls[0]["payload"])

    async def test_resume_restores_saved_result_without_reexecuting(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _FakeEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=Path(self._testMethodName + ".db"),
            environment=environment,
            fake_model=True,
            suite_run_id="suite-resume",
        )
        try:
            await runner.open()
            first = await runner.run_case(case, variants=["risk"], repeat_count=1)
            first_task_count = len(environment.tasks)
            await runner.close()

            resumed = AgentModelSuiteRunner(
                db_path=runner.db_path,
                environment=environment,
                fake_model=True,
                suite_run_id="suite-resume",
                resume=True,
            )
            await resumed.open()
            second = await resumed.run_case(case, variants=["risk"], repeat_count=1)
            await resumed.close()

            self.assertEqual(second[0].run_id, first[0].run_id)
            self.assertEqual(len(environment.tasks), first_task_count)
        finally:
            if runner._context_store is not None:
                await runner.close()
            runner.db_path.unlink(missing_ok=True)

    async def test_variant_visibility_changes_without_ground_truth_leakage(self):
        case = CaseLoader(DATASET).load_all()["CON-01"]
        environment = _FakeEnvironment()
        runner = AgentModelSuiteRunner(
            db_path=":memory:", environment=environment, fake_model=True,
        )
        await runner.open()
        try:
            await runner.run_case(case, repeat_count=1)
        finally:
            await runner.close()

        by_variant = {task.payload["variant"]: task for task in environment.tasks}
        self.assertIn("source_graph", by_variant["baseline"].payload["public_state"])
        self.assertNotIn("source_graph", by_variant["risk"].payload["public_state"])
        self.assertIn("source_graph", by_variant["recovery"].payload["public_state"])
        for task in environment.tasks:
            self.assertNotIn("ground_truth", str(task.payload))
            self.assertNotIn("expected_safe_behavior", str(task.payload))
            self.assertNotIn("variant_stage", str(task.payload["agent_visible"]))
            self.assertNotIn("variant_conditions", str(task.payload["agent_visible"]))
