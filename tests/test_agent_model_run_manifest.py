import argparse
import unittest

from scripts.run_agent_model_suite import build_run_manifest


class AgentModelRunManifestTest(unittest.TestCase):
    def test_manifest_contains_reproducibility_fields_and_all_case_hashes(self):
        args = argparse.Namespace(
            execution_mode="offline_deterministic",
            run_purpose="dev",
            variant="all",
            repeat_count=1,
            experiment_level="both",
        )
        manifest = build_run_manifest(args, "suite-test", ["CAS-01"])

        self.assertEqual(len(manifest["case_hashes"]), 160)
        self.assertTrue(manifest["git_commit"])
        self.assertTrue(manifest["dirty_diff_hash"])
        self.assertTrue(manifest["prompt_hash"])
        self.assertEqual(manifest["planned_order"], ["CAS-01"])
        self.assertEqual(
            len(manifest["dataset_split"]["formal_evaluation"]),
            160,
        )
        self.assertEqual(manifest["dataset_split"]["held_out"], [])
        self.assertTrue(
            manifest["dataset_provenance"][
                "all_cases_seen_during_evaluator_development"
            ]
        )
