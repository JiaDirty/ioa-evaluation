from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.export_agent_model_prompt_snapshots import _parser, _run


class AgentModelPromptSnapshotTest(unittest.IsolatedAsyncioTestCase):
    async def test_export_covers_all_variants_without_leakage(self):
        with TemporaryDirectory() as directory:
            args = _parser().parse_args([
                "--case-id", "CAS-01",
                "--case-id", "CON-01",
                "--output", directory,
            ])

            result = await _run(args)

            self.assertEqual(result["case_count"], 2)
            self.assertGreater(result["record_count"], 0)
            self.assertEqual(
                set(result["manifest"]["records_by_variant"]),
                {"baseline", "risk", "recovery"},
            )
            self.assertEqual(
                result["manifest"]["prompt_isolation_failure_count"], 0,
            )
            content = Path(result["files"]["jsonl"]).read_text(encoding="utf-8")
            self.assertIn('"model_visible"', content)
            self.assertIn('"tool_descriptors"', content)
            self.assertNotIn('"ground_truth"', content)
            self.assertNotIn('"expected_safe_behavior"', content)
            self.assertNotIn('"risk_type"', content)
