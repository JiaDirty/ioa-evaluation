import unittest
from pathlib import Path

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.tools.runtime_registry import register_case_tools
from src.gateway.gateway import _agent_visible_tool_result
from src.tools.gateway import ToolGateway
from src.tools.models import ToolCall


DATASET = Path(__file__).resolve().parents[1] / "data" / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"


class _Environment:
    def __init__(self):
        self.tool_gateway = ToolGateway()


class AgentModelToolRegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_agent_tool_replay_keeps_facts_but_hides_evaluator_answers(self):
        raw = {
            "supported": False,
            "canonical_fact": "受控事实结果",
            "canonical_safe_answer": "这是评分器结论",
            "minimum_required_verification": True,
            "fixture_hit": True,
        }

        visible = _agent_visible_tool_result(raw)

        self.assertEqual(visible, {
            "supported": False,
            "fact_result": "受控事实结果",
        })
        self.assertIn("canonical_safe_answer", raw)

    async def test_authoritative_fixture_is_callable_only_through_gateway(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)
        fixture = case.scenario.authoritative_tool_fixture

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "信息-02"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["claim_id"], "信息-02")
        self.assertNotIn(fixture.response["claim_id"], str(result.output))
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])

    async def test_authoritative_lookup_uses_the_single_visible_claim_id(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "信息-02"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["claim_id"], "信息-02")
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])

    async def test_cascade_lookup_answers_the_proposition_actually_queried(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        baseline = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "信息-01"},
        ))
        risk = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "信息-02"},
        ))
        recovery = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "信息-03"},
        ))

        self.assertEqual(baseline.output["claim_id"], "信息-01")
        self.assertTrue(baseline.output["supported"])
        self.assertEqual(
            baseline.output["canonical_fact"],
            case.scenario.variants.baseline.initial_information,
        )
        self.assertEqual(risk.output["claim_id"], "信息-02")
        self.assertFalse(risk.output["supported"])
        self.assertEqual(recovery.output["claim_id"], "信息-03")
        self.assertTrue(recovery.output["supported"])
        self.assertEqual(
            recovery.output["canonical_fact"],
            case.scenario.variants.recovery.correction,
        )

    async def test_rumor_lookup_uses_the_visible_information_id(self):
        case = CaseLoader(DATASET).load_all()["RUM-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "信息-02"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["claim_id"], "信息-02")
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])

    async def test_consensus_lookup_uses_the_assessment_target_id(self):
        case = CaseLoader(DATASET).load_all()["CON-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "说法-01"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.output["fixture_hit"])

    async def test_authoritative_lookup_miss_is_not_semantic_success(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "not-visible-in-this-case"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertFalse(result.output["fixture_hit"])
        self.assertFalse(result.output["semantic_success"])

    async def test_incentive_lookup_uses_the_visible_material_id(self):
        case = CaseLoader(DATASET).load_all()["INC-01"]
        environment = _Environment()
        register_case_tools(environment, case)
        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_business_status",
            arguments={"claim_id": "材料-01"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])
        self.assertNotIn("fixture_key", result.output)
        self.assertEqual(result.output["information_id"], "材料-01")

    async def test_consensus_and_norm_tools_are_case_scoped(self):
        cases = CaseLoader(DATASET).load_all()
        environment = _Environment()
        register_case_tools(environment, cases["CON-01"])
        self.assertIsNotNone(environment.tool_gateway.get_tool("query_source_lineage"))
        register_case_tools(environment, cases["NOR-01"])
        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_rule_status",
            arguments={"rule_or_exception_id": cases["NOR-01"].scenario.formal_rule.rule_id},
        ))
        self.assertEqual(result.output["status"], "active")

    async def test_norm_shared_memory_requires_local_rule_authority(self):
        case = CaseLoader(DATASET).load_all()["NOR-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        rejected = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="propose_shared_memory",
            arguments={
                "content": case.scenario.temporary_exception.content,
                "source_rule_or_exception_id": (
                    case.scenario.temporary_exception.exception_id
                ),
            },
        ))
        approved = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="propose_shared_memory",
            arguments={
                "content": case.scenario.formal_rule.content,
                "source_rule_or_exception_id": case.scenario.formal_rule.rule_id,
            },
        ))
        memories = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_shared_memory",
            arguments={},
        ))

        self.assertFalse(rejected.output["approved"])
        self.assertEqual(rejected.output["status"], "rejected")
        self.assertTrue(approved.output["approved"])
        self.assertEqual(len(memories.output["entries"]), 1)
        self.assertEqual(
            memories.output["entries"][0]["metadata"]["source_id"],
            "规则-01",
        )

    async def test_judgment_action_is_blocked_without_confirmation_fixture(self):
        case = CaseLoader(DATASET).load_all()["JUD-01"]
        environment = _Environment()
        register_case_tools(environment, case)
        action = case.scenario.high_impact_actions[0]

        confirmation = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="request_user_confirmation",
            arguments={"action": action, "scope": case.case_id, "current_round": 1},
        ))
        attempted = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="execute_high_impact_action",
            arguments={
                "action": action,
                "scope": case.case_id,
                "current_round": 1,
                "confirmation_token": "",
            },
        ))

        self.assertFalse(confirmation.output["confirmed"])
        self.assertNotIn("confirmation_token", confirmation.output)
        self.assertFalse(attempted.output["allowed"])
        self.assertEqual(attempted.output["status"], "blocked")

    async def test_discussion_board_tools_are_sandboxed_and_stateful(self):
        case = CaseLoader(DATASET).load_all()["DIS-01"]
        environment = _Environment()
        services = register_case_tools(environment, case)
        services["discussion_actor_context"].update({
            "role_id": case.scenario.participants[0].participant_id,
            "controller_group": case.scenario.participants[0].controller_group,
        })

        post = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="post_discussion_message",
            arguments={
                "content": "controlled sandbox post",
                "source_ids": [],
            },
        ))
        board = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="query_discussion_board",
            arguments={},
        ))

        self.assertEqual(post.status, "completed")
        self.assertTrue(post.output["semantic_success"])
        self.assertEqual(len(board.output["messages"]), 1)
        self.assertEqual(board.output["messages"][0]["content"], "controlled sandbox post")
