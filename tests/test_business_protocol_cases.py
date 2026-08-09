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


if __name__ == "__main__":
    unittest.main()
