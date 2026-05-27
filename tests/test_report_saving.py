import tempfile
import unittest
from pathlib import Path

from run_experiment import save_report


class ReportSavingTest(unittest.TestCase):
    def test_save_report_uses_unique_names_for_fast_scenario_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp)
            report = {
                "summary": {"total_tests": 0},
                "scenario": {"scenario_id": "ioa_example_001"},
            }

            first = save_report(report, str(output_dir))
            second = save_report(report, str(output_dir))

            self.assertNotEqual(first, second)
            self.assertEqual(len(list(output_dir.glob("*.json"))), 2)
