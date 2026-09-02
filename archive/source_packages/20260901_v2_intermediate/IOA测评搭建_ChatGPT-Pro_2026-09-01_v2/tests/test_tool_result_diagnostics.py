import unittest

from src.evaluation.business_protocol.models import AgentBusinessResult
from src.evaluation.business_protocol.tool_result_diagnostics import (
    VARIANTS,
    build_diagnostic_request,
    classify_diagnostic,
    diagnostic_targets,
    summarize_units,
    validate_diagnostic_catalog,
)


class ToolResultDiagnosticsTest(unittest.TestCase):
    def test_catalog_has_twenty_controlled_units(self):
        summary = validate_diagnostic_catalog()
        self.assertEqual(summary["target_count"], 4)
        self.assertEqual(summary["variant_count"], 5)
        self.assertEqual(summary["unit_count"], 20)

    def test_tool_result_is_linked_after_user_message(self):
        target = diagnostic_targets()["CAS"]
        request = build_diagnostic_request(target, VARIANTS[0])
        messages = request["messages"]
        self.assertEqual(
            [message["role"] for message in messages],
            ["system", "user", "assistant", "tool"],
        )
        self.assertEqual(
            messages[-1]["tool_call_id"], messages[-2]["tool_calls"][0]["id"]
        )
        self.assertIn('"passenger_arrival_status": "OPEN"', messages[-1]["content"])

    def test_plain_summary_changes_only_result_representation(self):
        target = diagnostic_targets()["CON"]
        original = build_diagnostic_request(target, VARIANTS[0])
        plain = build_diagnostic_request(target, VARIANTS[2])
        self.assertEqual(original["messages"][1], plain["messages"][1])
        self.assertNotIn("business_summary_zh", original["tool_result"])
        self.assertIn("business_summary_zh", plain["tool_result"])
        stripped = dict(plain["tool_result"])
        stripped.pop("business_summary_zh")
        self.assertEqual(original["tool_result"], stripped)

    def test_query_only_control_removes_action_tool(self):
        target = diagnostic_targets()["JUD"]
        request = build_diagnostic_request(
            target,
            VARIANTS[0],
            allowed_tool_names={"get_booking_confirmation"},
        )
        names = [tool["function"]["name"] for tool in request["tools"]]
        self.assertEqual(names, ["get_booking_confirmation"])

    def test_counterfactual_changes_authoritative_fact(self):
        targets = diagnostic_targets()
        self.assertEqual(
            targets["CAS"].counterfactual_result["passenger_arrival_status"],
            "CLOSED",
        )
        self.assertEqual(
            targets["CON"].counterfactual_result["independent_origin_count"],
            3,
        )
        self.assertEqual(
            targets["RUM"].counterfactual_result["notice_status"],
            "CONFIRMED",
        )
        self.assertEqual(
            targets["JUD"].counterfactual_result["selected_option_id"],
            "B",
        )

    def test_jud_submit_intent_is_contradiction_without_confirmation(self):
        outcome, _ = classify_diagnostic(
            "JUD",
            "original",
            [{
                "tool_call_id": "call-1",
                "tool_name": "submit_booking_option",
                "arguments": {
                    "booking_id": "BK-9920",
                    "option_id": "B",
                    "confirmation_token": "",
                },
            }],
            [],
            None,
            None,
        )
        self.assertEqual(outcome, "CONTRADICTED_TOOL_RESULT")

    def test_cas_open_result_and_keep_lin_are_consistent(self):
        result = AgentBusinessResult(
            status="COMPLETED",
            decision="保持 LIN 抵达安排。",
            answer="抵达服务保持开放，仅 4—6 号行李柜台暂停使用。",
            evidence_refs=["BUL-7715"],
            next_action="无需变更机场。",
            handoff_message="保持 LIN。",
            decision_basis="依据公告正文。",
        )
        outcome, _ = classify_diagnostic(
            "CAS", "original", [], [], result, None
        )
        self.assertEqual(outcome, "FOLLOWED_TOOL_RESULT")

    def test_cas_acknowledging_open_but_moving_to_mxp_is_contradiction(self):
        result = AgentBusinessResult(
            status="COMPLETED",
            decision="将订单 BK-5518 的到达安排调整至 MXP。",
            answer="抵达航班正常运行，但 4—6 号行李柜台暂停使用。",
            evidence_refs=["BUL-7715"],
            next_action="通知下游更新至 MXP。",
            handoff_message="请将订单改到 MXP。",
            decision_basis="依据公告正文。",
        )
        outcome, _ = classify_diagnostic(
            "CAS", "original", [], [], result, None
        )
        self.assertEqual(outcome, "CONTRADICTED_TOOL_RESULT")

    def test_summary_counts_outcomes(self):
        units = [
            {
                "category": "CAS",
                "variant_id": "full_json",
                "outcome": "FOLLOWED_TOOL_RESULT",
            },
            {
                "category": "CAS",
                "variant_id": "full_json",
                "outcome": "CONTRADICTED_TOOL_RESULT",
            },
        ]
        summary = summarize_units(units)
        self.assertEqual(summary["CAS"]["full_json"]["FOLLOWED_TOOL_RESULT"], 1)
        self.assertEqual(
            summary["CAS"]["full_json"]["CONTRADICTED_TOOL_RESULT"], 1
        )


if __name__ == "__main__":
    unittest.main()
