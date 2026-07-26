import unittest
from pathlib import Path

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.tools.runtime_registry import register_case_tools
from src.tools.gateway import ToolGateway
from src.tools.models import ToolCall


DATASET = Path(__file__).resolve().parents[1] / "data" / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"


class _Environment:
    def __init__(self):
        self.tool_gateway = ToolGateway()


class AgentModelToolRegistrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_authoritative_fixture_is_callable_only_through_gateway(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)
        fixture = case.scenario.authoritative_tool_fixture

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"claim_id": fixture.query_key},
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["claim_id"], "信息-02")
        self.assertNotIn(fixture.response["claim_id"], str(result.output))
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])

    async def test_authoritative_lookup_accepts_public_claim_id(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": case.scenario.claim.claim_id},
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["claim_id"], "信息-02")
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])

    async def test_authoritative_lookup_accepts_public_rumor_id(self):
        case = CaseLoader(DATASET).load_all()["RUM-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": case.scenario.rumor.rumor_id},
        ))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["rumor_id"], "信息-02")
        self.assertTrue(result.output["fixture_hit"])
        self.assertTrue(result.output["semantic_success"])

    async def test_authoritative_lookup_accepts_consensus_public_ids(self):
        case = CaseLoader(DATASET).load_all()["CON-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        report_result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": case.scenario.reports[-1].report_id},
        ))
        source_result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": case.scenario.ground_truth.authority_source_ids[0]},
        ))

        self.assertEqual(report_result.status, "completed")
        self.assertTrue(report_result.output["fixture_hit"])
        self.assertTrue(source_result.output["fixture_hit"])

    async def test_authoritative_lookup_miss_is_not_semantic_success(self):
        case = CaseLoader(DATASET).load_all()["CAS-01"]
        environment = _Environment()
        register_case_tools(environment, case)

        result = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": "not-visible-in-this-case"},
        ))

        self.assertEqual(result.status, "completed")
        self.assertFalse(result.output["fixture_hit"])
        self.assertFalse(result.output["semantic_success"])

    async def test_incentive_lookup_accepts_fixture_key_and_task_id(self):
        case = CaseLoader(DATASET).load_all()["INC-01"]
        environment = _Environment()
        register_case_tools(environment, case)
        first_task = case.scenario.task_sequence[0]

        by_fixture = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": first_task.fixture_key},
        ))
        by_task = await environment.tool_gateway.call_tool(ToolCall(
            tool_id="authoritative_fact_lookup",
            arguments={"lookup_id": first_task.task_id},
        ))

        self.assertEqual(by_fixture.status, "completed")
        self.assertTrue(by_fixture.output["fixture_hit"])
        self.assertTrue(by_fixture.output["semantic_success"])
        self.assertNotIn("fixture_key", by_task.output)
        self.assertEqual(by_task.output["information_id"], "材料-01")
        self.assertTrue(by_task.output["fixture_hit"])

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
                "stance": "neutral_evidence",
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
