import json
import unittest

from src.evaluation.business_protocol.cases import get_business_cases
from src.evaluation.business_protocol.runner import BusinessProtocolRunner
from src.evaluation.business_protocol.scripted_client import ScriptedBusinessClient


class BusinessProtocolRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def test_native_tool_call_and_result_are_replayed(self):
        case = get_business_cases()["travel-scope-001"]
        step = case.steps[0]
        client = ScriptedBusinessClient([
            {
                "tool_calls": [{
                    "id": "call-1",
                    "type": "function",
                    "function": {
                        "name": "query_airport_bulletin",
                        "arguments": json.dumps({"bulletin_id": "BUL-7715"}),
                    },
                }],
            },
            {
                "content": json.dumps({
                    "status": "COMPLETED",
                    "decision": "按公告正文形成交接。",
                    "answer": "公告只涉及航站楼设施，不改变到达机场。",
                    "evidence_refs": ["BUL-7715", "QUERY-BUL-7715-01"],
                    "next_action": "交给行程规划角色。",
                    "handoff_message": "保持 LIN 到达安排。",
                }, ensure_ascii=False),
            },
        ])
        runner = BusinessProtocolRunner(client)

        trace = await runner.run_step(case, step, "mechanism")

        self.assertEqual(trace.validity, "VALID")
        self.assertEqual(len(trace.tool_intents), 1)
        self.assertEqual(trace.tool_results[0].execution_status, "SUCCEEDED")
        second_messages = client.calls[1]["messages"]
        self.assertEqual(second_messages[-2]["role"], "assistant")
        self.assertEqual(second_messages[-1]["role"], "tool")
        self.assertEqual(second_messages[-1]["tool_call_id"], "call-1")
        self.assertEqual(
            set(trace.final_result.model_dump()),
            {
                "status",
                "decision",
                "answer",
                "evidence_refs",
                "next_action",
                "handoff_message",
            },
        )

    async def test_invalid_output_is_failed_run_not_safe(self):
        case = get_business_cases()["travel-scope-001"]
        client = ScriptedBusinessClient([{"content": "not-json"}])
        runner = BusinessProtocolRunner(client)

        trace = await runner.run_step(case, case.steps[0], "mechanism")

        self.assertEqual(trace.validity, "INVALID")
        self.assertIsNone(trace.safety_outcome)
        self.assertIsNotNone(trace.error)


if __name__ == "__main__":
    unittest.main()
