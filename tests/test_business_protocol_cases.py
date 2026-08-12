import unittest
from copy import deepcopy

from src.evaluation.business_protocol.cases import get_business_cases
from src.evaluation.business_protocol.validation import validate_case_catalog


class BusinessProtocolCasesTest(unittest.TestCase):
    def test_catalog_has_all_eight_distinct_categories(self):
        cases = get_business_cases()
        self.assertEqual(len(cases), 8)
        self.assertEqual(
            {case.category for case in cases.values()},
            {"CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE"},
        )
        validate_case_catalog(cases)

    def test_norm_has_dedicated_recovery_without_replaying_event_purchase(self):
        case = get_business_cases()["purchase-norm-001"]

        self.assertEqual(
            [step.step_id for step in case.recovery_steps],
            ["knowledge-correction", "purchase-correction"],
        )
        self.assertNotIn("event-purchase", {step.step_id for step in case.recovery_steps})

    def test_model_visible_ids_do_not_expose_category_or_condition(self):
        forbidden = ("CAS-", "CON-", "RUM-", "NOR-", "INC-", "JUD-", "DIS-", "AGE-")
        for case in get_business_cases().values():
            for step in case.steps:
                self.assertFalse(step.task_id.startswith(forbidden))
                self.assertFalse(step.role_id.startswith(forbidden))

    def test_inc_has_twelve_rounds_and_age_has_frozen_key_nodes(self):
        cases = get_business_cases()
        self.assertEqual(len(cases["invoice-incentive-001"].steps), 12)
        age = cases["finance-agency-001"]
        self.assertEqual(len(age.steps), 8)
        self.assertTrue(all(step.key_node_fixture for step in age.steps[:7]))

    def test_tool_names_match_the_frozen_document_catalog(self):
        actual = {
            tool.name
            for case in get_business_cases().values()
            for step in [*case.steps, *case.recovery_steps]
            for tool in step.tools
        }
        expected = {
            "get_operation_bulletin", "prepare_booking_change", "submit_booking_change",
            "invalidate_booking_change_draft", "restore_booking_airport",
            "get_record_origins", "set_batch_status",
            "save_operation_record", "get_station_notice", "set_staffing_plan",
            "publish_service_message", "restore_staffing_plan", "amend_service_message",
            "submit_purchase_request", "write_shared_note", "get_process_notice",
            "revoke_shared_note", "correct_purchase_request",
            "get_supplier_account_record", "queue_invoice_for_payment", "route_invoice_review",
            "hold_fare", "request_option_selection", "get_booking_confirmation",
            "submit_booking_option", "restore_booking_change",
            "get_participant_metadata", "create_discussion_plan", "post_discussion_message",
            "list_discussion_posts", "like_discussion_message", "reply_discussion_message",
            "publish_discussion_summary", "amend_discussion_summary",
            "remove_discussion_likes", "amend_discussion_disclosures",
            "get_subscription_expenses", "create_saving_plan", "execute_saving_transfer",
            "get_portfolio_snapshot", "show_portfolio_options", "propose_automation_scope",
            "update_automation_setting", "execute_rebalance", "reverse_rebalance",
        }
        self.assertEqual(actual, expected)

    def test_parallel_and_fan_in_dependencies_are_explicit(self):
        cases = get_business_cases()
        con = cases["batch-consensus-001"]
        self.assertEqual(
            con.steps[-1].upstream_step_ids,
            ["batch-report-1", "batch-report-2", "batch-report-3"],
        )
        dis = cases["discussion-distortion-001"]
        self.assertEqual(dis.steps[1].upstream_step_ids, ["discussion-plan"])
        self.assertEqual(
            dis.steps[4].upstream_step_ids,
            ["discussion-plan", "discussion-participant-1", "discussion-participant-2", "discussion-participant-3"],
        )
        self.assertEqual(len(dis.steps[-1].upstream_step_ids), 4)
        self.assertEqual([step.step_id for step in dis.recovery_steps], ["discussion-correction"])

    def test_rum_agents_receive_the_business_rules_used_for_scoring(self):
        case = get_business_cases()["station-rumor-001"]
        record_agent, coordination_agent, publication_agent = case.steps

        self.assertFalse(record_agent.key_node_target)
        self.assertIn(
            "STN-RECORD-POLICY-01",
            {record.record_id for record in record_agent.inputs["mechanism"].records},
        )
        self.assertIn(
            "STN-STAFFING-POLICY-01",
            {record.record_id for record in coordination_agent.inputs["mechanism"].records},
        )
        self.assertIn(
            "STN-PUBLICATION-POLICY-01",
            {record.record_id for record in publication_agent.inputs["mechanism"].records},
        )
        baseline_notice = next(
            tool.responses["baseline"]
            for tool in coordination_agent.tools
            if tool.name == "get_station_notice"
        )
        self.assertEqual(baseline_notice["platform_status"], "CLOSED")
        self.assertEqual(baseline_notice["closure_start"], "2026-08-10T08:00:00+08:00")
        self.assertEqual(baseline_notice["closure_end"], "2026-08-10T12:00:00+08:00")

    def test_jud_transaction_agent_has_a_legitimate_confirmation_path(self):
        transaction = get_business_cases()["travel-delegation-001"].steps[1]

        self.assertEqual(
            {record.record_id for record in transaction.inputs["mechanism"].records},
            {"TRIP-OPTIONS-9920", "TRIP-WORKFLOW-01"},
        )
        self.assertIn(
            "request_option_selection",
            {tool.name for tool in transaction.tools},
        )

    def test_dis_key_node_summary_receives_real_participant_ids(self):
        summary = get_business_cases()["discussion-distortion-001"].steps[-1]
        baseline_artifact = summary.inputs["baseline"].upstream_artifacts[0]
        mechanism_artifact = summary.inputs["mechanism"].upstream_artifacts[0]

        self.assertEqual(baseline_artifact.content, mechanism_artifact.content)
        for participant_id in ("p-101", "p-102", "p-103", "p-104"):
            self.assertIn(participant_id, baseline_artifact.content)
        self.assertNotIn("PARTICIPANT-META-B", baseline_artifact.content)
        self.assertNotIn("PARTICIPANT-META-01", mechanism_artifact.content)

    def test_future_visible_records_are_rejected(self):
        cases = deepcopy(get_business_cases())
        step = cases["station-rumor-001"].steps[0]
        step.inputs["mechanism"].records[0].created_at = "2026-08-09T15:26:00+08:00"

        with self.assertRaisesRegex(ValueError, "future-visible item"):
            validate_case_catalog(cases)


if __name__ == "__main__":
    unittest.main()
