import unittest

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
            "like_discussion_message", "publish_discussion_summary", "amend_discussion_summary",
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
        self.assertTrue(all(
            step.upstream_step_ids == ["discussion-plan"]
            for step in dis.steps[1:5]
        ))
        self.assertEqual(len(dis.steps[-1].upstream_step_ids), 4)


if __name__ == "__main__":
    unittest.main()
