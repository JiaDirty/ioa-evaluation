from pathlib import Path
import unittest

from src.core.data_models import Artifact, TaskResult, TaskStatus
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.step_executor import AgentModelStepExecutor, RunContext
from src.evaluation.agent_model.step_executor import StepExecutionError
from src.evaluation.agent_model.context_store import AgentContextStore

DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class _FakeEnvironment:
    def __init__(self):
        self.tasks = []

    async def submit_task(self, task):
        self.tasks.append(task)
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="fake-agent",
            content={"text": f"result-{len(self.tasks)}"},
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=artifact.content,
            artifacts=[artifact],
            participating_agents=["fake-agent"],
        )

    def get_agent_sub_ioa(self, agent_id):
        return "news"


class _TextEnvironment(_FakeEnvironment):
    async def submit_task(self, task):
        result = await super().submit_task(task)
        return result.model_copy(update={"output": {"text": "plain unstructured answer"}})


class _DirectBehaviorEnvironment(_FakeEnvironment):
    async def submit_task(self, task):
        raw_behavior = {
            "claims": [{
                "claim_id": "claim-cas-01:baseline",
                "statement": "正常运行",
                "disposition": "accepted",
                "forwarded": False,
                "source_label": "authority-cas-01",
                "verification_status": "verified",
            }],
            "sources": [],
            "verification_requested": True,
            "rules_used": [],
            "exceptions_used": [],
            "alternatives_presented": [],
            "human_confirmation_required": False,
            "permission_expansion_requested": False,
            "discussion_actions": [],
            "notes": [],
        }
        trace = {
            "response": {
                "raw": raw_behavior,
                "parsed": raw_behavior,
                "error": None,
            }
        }
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="fake-agent",
            content={"synthesis": "internal output must not replace model evidence"},
            metadata={"model_call_traces": [trace]},
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output={"synthesis": "internal output must not replace model evidence"},
            artifacts=[artifact],
            participating_agents=["fake-agent"],
        )


class _FakeToolCallStore:
    def list_by_task(self, task_id):
        return [{"task_id": task_id, "tool_id": "authoritative_fact_lookup"}]


class AgentModelStepExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_direct_behavior_record_is_wrapped_without_inventing_answer(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        executor = AgentModelStepExecutor(_DirectBehaviorEnvironment())

        result = await executor.run_step(
            RunContext("run-direct-behavior", case, "baseline"),
            "upstream_intelligence", "news", "news_aggregation", "task",
        )

        self.assertEqual(result["output"]["type"], "final")
        self.assertEqual(result["output"]["business_output"]["answer"], "")
        self.assertEqual(
            result["output"]["behavior_record"]["claims"][0]["claim_id"],
            "claim-cas-01:baseline",
        )
        self.assertNotIn("internal output", str(result["output"]))
        self.assertEqual(
            result["model_call_traces"][0]["response"]["raw"]["claims"][0]["statement"],
            "正常运行",
        )
        self.assertIsNone(result["behavior_parse_error"])

    async def test_submits_real_task_and_forwards_full_artifact(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        env = _FakeEnvironment()
        executor = AgentModelStepExecutor(env)
        context = RunContext("run-1", case, "risk")

        first = await executor.run_step(
            context,
            "upstream_intelligence",
            "news",
            "news_aggregation",
            "first task",
            allowed_tool_ids=["authoritative_fact_lookup"],
        )
        second = await executor.run_step(
            context,
            "risk_analysis",
            "finance",
            "risk_assessment",
            "second task",
            upstream_artifact_ids=[first["artifact_id"]],
        )

        self.assertEqual(len(env.tasks), 2)
        self.assertEqual(first["output"], {"text": "result-1"})
        self.assertEqual(second["output"], {"text": "result-2"})
        self.assertEqual(
            env.tasks[1].payload["upstream_artifacts"][0]["content"],
            {"text": "result-1"},
        )
        self.assertEqual(
            env.tasks[0].payload["allowed_tool_ids"],
            ["authoritative_fact_lookup"],
        )
        self.assertTrue(
            env.tasks[0].payload["controlled_agent_model_evaluation_step"]
        )
        self.assertEqual(
            env.tasks[0].task_spec.intent,
            "controlled_agent_model_evaluation",
        )
        self.assertEqual(
            env.tasks[0].task_spec.capability_requirements[0].capability,
            "news_aggregation",
        )

    async def test_paired_role_binding_is_forwarded_to_gateway(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        env = _FakeEnvironment()
        bindings = {"upstream_intelligence": "fixed-agent"}
        executor = AgentModelStepExecutor(env, role_agent_bindings=bindings)

        await executor.run_step(
            RunContext("run-binding", case, "risk"),
            "upstream_intelligence", "news", "news_aggregation", "task",
        )

        self.assertEqual(
            env.tasks[0].payload["evaluation_preferred_agent_id"],
            "fixed-agent",
        )

    async def test_collects_tool_calls_from_environment_store(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        env = _FakeEnvironment()
        env.tool_call_store = _FakeToolCallStore()
        executor = AgentModelStepExecutor(env)

        result = await executor.run_step(
            RunContext("run-tools", case, "risk"),
            "upstream_intelligence",
            "news",
            "news_aggregation",
            "first task",
            allowed_tool_ids=["authoritative_fact_lookup"],
        )

        self.assertEqual(
            result["tool_calls"][0]["tool_id"],
            "authoritative_fact_lookup",
        )

    async def test_records_formal_parse_failure_without_discarding_raw_output(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        executor = AgentModelStepExecutor(_TextEnvironment())
        result = await executor.run_step(
            RunContext("run-parse", case, "risk"),
            "upstream_intelligence", "news", "news_aggregation", "task",
        )
        self.assertIn("invalid behavior JSON", result["behavior_parse_error"])
        self.assertEqual(result["output"]["text"], "plain unstructured answer")
        self.assertEqual(len(executor.parse_failures), 1)

    async def test_context_history_persists_only_agent_visible_input(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            executor = AgentModelStepExecutor(_FakeEnvironment(), store)
            context = RunContext("run-visible", case, "risk")
            await executor.run_step(
                context,
                "upstream_intelligence",
                "news",
                "news_aggregation",
                "task",
            )
            session_id = store.get_session_id("run-visible", "upstream_intelligence")
            turn = store.get_all_turns(session_id)[0]
            self.assertIn("agent_visible", turn["input_json"])
            self.assertNotIn("risk_type", str(turn["input_json"]))
            self.assertNotIn("variant", str(turn["input_json"]))
            self.assertTrue(store.list_events("run-visible"))
        finally:
            await store.close()

    async def test_model_call_budget_is_enforced_across_steps(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        execution_config = case.execution_config.model_copy(update={
            "max_agent_calls_per_case": 1,
        })
        case = case.model_copy(update={"execution_config": execution_config})
        executor = AgentModelStepExecutor(_FakeEnvironment())
        context = RunContext("run-budget", case, "risk")
        await executor.run_step(context, "one", "news", "news_aggregation", "one")
        with self.assertRaisesRegex(StepExecutionError, "budget exceeded") as raised:
            await executor.run_step(context, "two", "news", "news_aggregation", "two")
        self.assertEqual(raised.exception.failure_code, "INVALID_BUDGET_EXCEEDED")
