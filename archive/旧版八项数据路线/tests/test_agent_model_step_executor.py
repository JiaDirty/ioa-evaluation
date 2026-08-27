from pathlib import Path
import unittest

from src.core.data_models import Artifact, TaskResult, TaskStatus
from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.step_executor import (
    AgentModelStepExecutor,
    RunContext,
    _compact_turn_history,
    _latest_final_model_output,
)
from src.evaluation.agent_model.step_executor import StepExecutionError
from src.evaluation.agent_model.context_store import AgentContextStore

DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "generated"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class _FakeEnvironment:
    def __init__(self):
        self.tasks = []

    async def submit_task(self, task):
        self.tasks.append(task)
        output = {
            "status": "COMPLETED",
            "decision": f"完成第 {len(self.tasks)} 个步骤。",
            "answer": f"result-{len(self.tasks)}",
            "evidence_refs": [],
            "next_action": "交给下一角色继续处理",
            "handoff_message": f"result-{len(self.tasks)}",
        }
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id="fake-agent",
            content=output,
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
        raw_output = {
            "status": "COMPLETED",
            "decision": "记录当前结果。",
            "answer": "正常运行",
            "evidence_refs": ["权威来源-01"],
            "next_action": "完成本步骤",
            "handoff_message": "",
        }
        trace = {
            "response": {
                "raw": raw_output,
                "parsed": raw_output,
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
        return [{"task_id": task_id, "tool_id": "query_business_status"}]


class AgentModelStepExecutorTest(unittest.IsolatedAsyncioTestCase):
    async def test_connection_failure_is_classified_as_api_failure(self):
        class ConnectionFailureEnvironment(_FakeEnvironment):
            async def submit_task(self, task):
                self.tasks.append(task)
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error="Connection error.",
                )

        case = CaseLoader(DATASET).load_all()["AGE-01"]
        executor = AgentModelStepExecutor(ConnectionFailureEnvironment())

        with self.assertRaisesRegex(StepExecutionError, "Connection error") as raised:
            await executor.run_step(
                RunContext("run-connection-failure", case, "baseline"),
                "domain_agent", "finance", "domain_analysis", "task",
            )

        self.assertEqual(raised.exception.failure_code, "INVALID_API_FAILURE")

    async def test_failed_step_preserves_executed_and_duplicate_tool_calls(self):
        class FailedAfterToolEnvironment(_FakeEnvironment):
            async def submit_task(self, task):
                self.tasks.append(task)
                tool_result = {
                    "call_id": "call-1",
                    "tool_id": "query_business_status",
                    "status": "completed",
                    "output": {
                        "claim_id": "信息-01",
                        "supported": True,
                    },
                    "error": None,
                }
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    output={"invalid_response": {"action": {"kind": "final"}}},
                    participating_agents=["fake-agent"],
                    error="invalid AgentModelAction on tested response",
                    metadata={
                        "tested_response_policy": "first_response_only",
                        "format_correction_attempted": False,
                        "model_call_traces": [{
                            "request": {"messages": []},
                            "response": {"raw": {"action": {"kind": "tool_call"}}},
                        }, {
                            "request": {"messages": []},
                            "response": {"raw": {"action": {"kind": "final"}}},
                        }],
                        "executed_tool_calls": [{
                            "turn": 1,
                            "requested_action": {
                                "type": "tool_call",
                                "tool_id": "query_business_status",
                                "arguments": {"claim_id": "信息-01"},
                                "reason": "核验信息",
                            },
                            "result": tool_result,
                        }],
                        "duplicate_tool_calls": [{
                            "turn": 2,
                            "tool_id": "query_business_status",
                            "arguments": {"claim_id": "信息-01"},
                            "executed_again": False,
                        }],
                    },
                )

        case = CaseLoader(DATASET).load_all()["CAS-01"]
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            executor = AgentModelStepExecutor(FailedAfterToolEnvironment(), store)
            context = RunContext("run-failed-after-tool", case, "baseline")
            with self.assertRaises(StepExecutionError):
                await executor.run_step(
                    context,
                    "upstream_intelligence",
                    "news",
                    "news_aggregation",
                    "task",
                    public_state={
                        "active_information": {
                            "claim_id": "信息-01",
                            "statement": "机场当前正常运行",
                        },
                    },
                    allowed_tool_ids=["query_business_status"],
                    max_tool_calls=1,
                    required_claim_id="信息-01",
                )

            observation = executor.observations[0]
            self.assertEqual(len(observation["tool_calls"]), 1)
            self.assertEqual(
                observation["tool_calls"][0]["tool_id"],
                "query_business_status",
            )
            self.assertEqual(len(observation["duplicate_tool_calls"]), 1)
            self.assertEqual(executor.tool_call_count, 1)

            events = store.list_events(context.run_id)
            self.assertEqual(
                [event["event_type"] for event in events].count("tool_call"),
                1,
            )
            self.assertEqual(
                [event["event_type"] for event in events].count("tool_result"),
                1,
            )
            agent_call = next(
                event for event in events if event["event_type"] == "agent_call"
            )
            self.assertEqual(len(agent_call["payload"]["duplicate_tool_calls"]), 1)

            session_id = store.get_session_id(
                context.run_id, "upstream_intelligence"
            )
            turn = store.get_all_turns(session_id)[0]
            self.assertEqual(len(turn["tool_calls_json"]), 1)
            self.assertEqual(
                len(turn["output_json"]["duplicate_tool_calls"]), 1
            )
        finally:
            await store.close()

    async def test_duplicate_tool_calls_are_recovered_when_adapter_metadata_is_empty(self):
        class AdapterDropsDuplicateMetadataEnvironment(_FakeEnvironment):
            async def submit_task(self, task):
                tool_action = {
                    "action": {
                        "kind": "tool_call",
                        "tool_call": {
                            "tool_id": "query_business_status",
                            "arguments": {"claim_id": "信息-01"},
                            "reason": "查询当前信息",
                        },
                    }
                }
                final_action = {
                    "type": "final",
                    "business_output": {
                        "answer": "已完成本轮信息判断。",
                        "decision": "记录判断",
                        "confidence": 0.8,
                        "limitations": [],
                    },
                    "behavior_record": {},
                    "reason": "依据当前材料完成判断。",
                }
                traces = [
                    {"response": {"raw": tool_action, "parsed": tool_action}},
                    {"response": {"raw": tool_action, "parsed": tool_action}},
                    {"response": {"raw": final_action, "parsed": final_action}},
                ]
                artifact = Artifact(
                    task_id=task.task_id,
                    producer_agent_id="fake-agent",
                    content=final_action,
                    metadata={"model_call_traces": traces},
                )
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    output=final_action,
                    artifacts=[artifact],
                    participating_agents=["fake-agent"],
                    metadata={
                        "model_call_traces": traces,
                        "executed_tool_calls": [{
                            "turn": 1,
                            "requested_action": {
                                "type": "tool_call",
                                "tool_id": "query_business_status",
                                "arguments": {"claim_id": "信息-01"},
                                "reason": "查询当前信息",
                            },
                            "result": {
                                "call_id": "call-1",
                                "status": "completed",
                                "output": {"supported": True},
                                "error": None,
                            },
                        }],
                        "duplicate_tool_calls": [],
                    },
                )

        case = CaseLoader(DATASET).load_all()["CAS-01"]
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            executor = AgentModelStepExecutor(
                AdapterDropsDuplicateMetadataEnvironment(), store
            )
            result = await executor.run_step(
                RunContext("run-infer-duplicate", case, "baseline"),
                "upstream_intelligence",
                "news",
                "news_aggregation",
                "判断当前信息",
                allowed_tool_ids=["query_business_status"],
            )

            self.assertEqual(result["duplicate_tool_calls"], [{
                "turn": 2,
                "tool_id": "query_business_status",
                "arguments": {"claim_id": "信息-01"},
                "executed_again": False,
            }])
            session_id = store.get_session_id(
                "run-infer-duplicate", "upstream_intelligence"
            )
            turn = store.get_all_turns(session_id)[0]
            self.assertEqual(
                turn["output_json"]["duplicate_tool_calls"],
                result["duplicate_tool_calls"],
            )
        finally:
            await store.close()

    def test_history_replay_filters_internal_tool_result_fields(self):
        """Replayed turn history must never expose evaluator-internal fields."""
        turn = {
            "round_index": 1,
            "input_json": {
                "task_text": "任务",
                "visible_input": {"public_state": {}, "role_state": {}},
            },
            "output_json": {"step_output": {"answer": "已判断"}, "status": "completed"},
            "tool_calls_json": [{
                "tool_id": "query_business_status",
                "status": "completed",
                "arguments": {"claim_id": "说法-01"},
                "result": {
                    "status": "completed",
                    "output": {
                        "canonical_answer": "标准答案：应拒绝虚假多数。",
                        "canonical_fact": "正常运行",
                        "recovery_evidence_description": "内部恢复证据描述",
                        "supported": False,
                        "lookup_id": "说法-01",
                        "independent_source_count": 2,
                        "semantic_success": True,
                        "query_key": "CON:CON-01:official_records",
                    },
                },
            }],
        }
        compact = _compact_turn_history([turn])
        text = str(compact)
        self.assertNotIn("recovery_evidence_description", text)
        self.assertNotIn("内部恢复证据描述", text)
        self.assertNotIn("canonical_answer", text)
        self.assertNotIn("标准答案", text)
        self.assertNotIn("canonical_fact", text)
        self.assertNotIn("semantic_success", text)
        self.assertNotIn("query_key", text)
        # Canonical factual result is renamed, neutral business fields survive.
        self.assertIn("tool_answer", text)
        self.assertIn("正常运行", text)
        self.assertIn("independent_source_count", text)
        self.assertIn("说法-01", text)

    def test_history_compacts_repeated_board_and_feedback(self):
        compact = _compact_turn_history([{
            "round_index": 4,
            "input_json": {"visible_input": {"public_state": {
                "round": 4,
                "discussion_board": [
                    {"message_id": "msg-1", "content": "old long content"},
                    {"message_id": "msg-2", "content": "another old message"},
                ],
                "recent_performance_feedback": [
                    {"round": 1, "score": 0.5},
                    {"round": 2, "score": 0.6},
                ],
            }}},
            "output_json": {"step_output": {"decision": "keep"}},
            "tool_calls_json": [{
                "tool_id": "post_discussion_message",
                "status": "completed",
                "arguments": {
                    "content": "old long content",
                    "source_ids": ["证据-01"],
                    "stance": "neutral_evidence",
                },
                "result": {
                    "status": "completed",
                    "output": {
                        "message_id": "msg-2",
                        "content": "another old message",
                    },
                },
            }],
        }])

        state = compact[0]["input_json"]["public_state"]
        self.assertEqual(state["discussion_board"]["message_count"], 2)
        self.assertEqual(state["recent_performance_feedback"]["entry_count"], 2)
        self.assertNotIn("old long content", str(compact))
        self.assertNotIn("another old message", str(compact))
        self.assertIn("decision", str(compact))
        self.assertEqual(
            compact[0]["tool_calls"][0]["result"]["output"]["message_id"],
            "msg-2",
        )

    async def test_chain_stops_when_free_text_output_has_no_handoff(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]

        class MissingForwardEnvironment(_FakeEnvironment):
            async def submit_task(self, task):
                claim = task.payload["public_state"]["active_information"]
                output = {
                    "status": "NEEDS_INPUT",
                    "decision": "当前信息尚未确认，暂不交接。",
                    "answer": claim["statement"],
                    "evidence_refs": [claim["claim_id"]],
                    "next_action": "等待补充资料",
                    "handoff_message": "",
                }
                artifact = Artifact(
                    task_id=task.task_id,
                    producer_agent_id="fake-agent",
                    content=output,
                )
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    output=output,
                    artifacts=[artifact],
                    participating_agents=["fake-agent"],
                )

        executor = AgentModelStepExecutor(MissingForwardEnvironment())
        context = RunContext("run-missing-forward", case, "risk")
        chain = [
            {
                "role_id": "first",
                "public_state": {"active_information": {
                    "claim_id": "信息-02", "statement": "待判断信息",
                }},
                "forward_claim_id": "信息-02",
            },
            {
                "role_id": "second",
                "public_state": {},
                "forward_claim_id": "信息-02",
            },
        ]

        results = await executor.run_chain(context, chain)
        self.assertEqual(len(results), 1)
        claim = results[0]["derived_behavior_record"]["claims"][0]
        self.assertFalse(claim["forwarded"])

    async def test_direct_six_field_response_is_preserved(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        executor = AgentModelStepExecutor(_DirectBehaviorEnvironment())

        result = await executor.run_step(
            RunContext("run-direct-behavior", case, "baseline"),
            "upstream_intelligence", "news", "news_aggregation", "task",
        )

        self.assertEqual(result["output"]["answer"], "正常运行")
        self.assertEqual(result["output"]["decision"], "记录当前结果。")
        self.assertNotIn("internal output", str(result["output"]))
        self.assertEqual(
            result["model_call_traces"][0]["response"]["raw"]["answer"],
            "正常运行",
        )
        self.assertIsNone(result["behavior_parse_error"])

    def test_tool_action_is_not_converted_to_empty_final_answer(self):
        raw_tool_action = {
            "action": {
                "kind": "tool_call",
                "tool_call": {
                    "tool_id": "query_business_status",
                    "arguments": {"claim_id": "claim-cas-01"},
                    "reason": "check the claim",
                },
            },
            "business_output": {},
            "behavior_record": {"verification_requested": True},
            "reason": "check before answering",
        }

        output = _latest_final_model_output([{
            "response": {"raw": raw_tool_action},
        }])

        self.assertIsNone(output)

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
            allowed_tool_ids=["query_business_status"],
            max_tool_calls=1,
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
        self.assertEqual(first["output"]["answer"], "result-1")
        self.assertEqual(second["output"]["answer"], "result-2")
        self.assertEqual(
            env.tasks[1].payload["upstream_artifacts"][0]["content"],
            first["output"],
        )
        self.assertEqual(
            env.tasks[0].payload["allowed_tool_ids"],
            ["query_business_status"],
        )
        expected_turns = min(
            2,
            case.execution_config.max_tool_rounds_per_agent + 1,
            case.execution_config.max_agent_calls_per_case,
            case.execution_config.cost_budget.max_total_model_calls,
            12,
        )
        self.assertEqual(env.tasks[0].constraints.max_agent_turns, expected_turns)
        self.assertEqual(
            env.tasks[0].task_spec.constraints.max_agent_turns,
            expected_turns,
        )
        normal_action = env.tasks[0].payload["visible_action_schema"][
            "properties"
        ]["action"]
        final_schema = env.tasks[0].payload["final_action_schema"]
        self.assertIn("anyOf", normal_action)
        self.assertEqual(set(final_schema["properties"]), {
            "status", "decision", "answer", "evidence_refs",
            "next_action", "handoff_message",
        })
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
            allowed_tool_ids=["query_business_status"],
        )

        self.assertEqual(
            result["tool_calls"][0]["tool_id"],
            "query_business_status",
        )

    async def test_records_formal_parse_failure_without_discarding_raw_output(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        executor = AgentModelStepExecutor(_TextEnvironment())
        result = await executor.run_step(
            RunContext("run-parse", case, "risk"),
            "upstream_intelligence", "news", "news_aggregation", "task",
        )
        self.assertIn("invalid decision JSON", result["behavior_parse_error"])
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

    async def test_success_step_records_first_response_policy_fields(self):
        """Normal completed steps must record the first-response policy."""
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        executor = AgentModelStepExecutor(_FakeEnvironment())
        result = await executor.run_step(
            RunContext("run-policy", case, "baseline"),
            "upstream_intelligence",
            "news",
            "news_aggregation",
            "task",
        )
        self.assertEqual(
            result["tested_response_policy"], "first_response_only"
        )
        self.assertFalse(result["format_correction_attempted"])

    async def test_success_step_records_semantic_consistency_errors_everywhere(self):
        class SemanticIssueEnvironment(_DirectBehaviorEnvironment):
            async def submit_task(self, task):
                result = await super().submit_task(task)
                return result.model_copy(update={
                    "metadata": {
                        **result.metadata,
                        "semantic_consistency_errors": [
                            "文字说明中的传递决定与 claims.forwarded 的值相反。"
                        ],
                        "tested_response_policy": "first_response_only",
                        "format_correction_attempted": False,
                    }
                })

        case = CaseLoader(DATASET).load_all()["CAS-01"]
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            executor = AgentModelStepExecutor(SemanticIssueEnvironment(), store)
            result = await executor.run_step(
                RunContext("run-semantic-issue", case, "baseline"),
                "upstream_intelligence",
                "news",
                "news_aggregation",
                "task",
            )

            self.assertTrue(result["semantic_consistency_errors"])
            agent_call = next(
                event for event in store.list_events("run-semantic-issue")
                if event["event_type"] == "agent_call"
            )
            self.assertTrue(
                agent_call["payload"]["semantic_consistency_errors"]
            )
            session_id = store.get_session_id(
                "run-semantic-issue", "upstream_intelligence"
            )
            turn = store.get_all_turns(session_id)[0]
            self.assertTrue(
                turn["output_json"]["semantic_consistency_errors"]
            )
        finally:
            await store.close()

    async def test_tool_call_step_records_first_response_policy_fields(self):
        """Tool-call steps must also record the first-response policy."""
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        env = _FakeEnvironment()
        env.tool_call_store = _FakeToolCallStore()
        executor = AgentModelStepExecutor(env)
        result = await executor.run_step(
            RunContext("run-policy-tools", case, "risk"),
            "upstream_intelligence",
            "news",
            "news_aggregation",
            "task",
            allowed_tool_ids=["query_business_status"],
        )
        self.assertTrue(result["tool_calls"])
        self.assertEqual(
            result["tested_response_policy"], "first_response_only"
        )
        self.assertFalse(result["format_correction_attempted"])

    async def test_claim_missing_step_records_first_response_policy_fields(self):
        """Claim evidence is derived without requiring model self-report fields."""
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        env = _FakeEnvironment()
        executor = AgentModelStepExecutor(env)
        results = await executor.run_chain(
            RunContext("run-policy-chain", case, "baseline"), [{
                "role_id": "upstream_intelligence",
                "sub_ioa_id": "news",
                "capability": "information_collection",
                "task_text": "task",
                "public_state": {
                    "active_information": {
                        "claim_id": "信息-01",
                        "statement": "正常运行",
                        "source_materials": [],
                    }
                },
                "role_state": {},
                "allowed_tool_ids": ["query_business_status"],
                "forward_claim_id": "信息-01",
            }, {
                "role_id": "risk_analysis",
                "sub_ioa_id": "news",
                "capability": "domain_analysis",
                "task_text": "task2",
                "public_state": {
                    "required_claim_assessment": {
                        "claim_id": "信息-01",
                        "copy_identifier_exactly": True,
                        "assess_only_from_upstream_artifact": True,
                    }
                },
                "role_state": {},
                "allowed_tool_ids": [],
                "forward_claim_id": "信息-01",
            }],
        )

        self.assertEqual(len(results), 2)
        observations = executor.observations
        self.assertTrue(observations)
        self.assertEqual(
            observations[0].get("tested_response_policy"), "first_response_only"
        )
        self.assertFalse(observations[0].get("format_correction_attempted"))
        claims = observations[0]["derived_behavior_record"]["claims"]
        self.assertEqual(claims[0]["claim_id"], "信息-01")

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
