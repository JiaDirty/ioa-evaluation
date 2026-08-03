import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scripts.run_agent_model_suite import build_run_manifest, save_results
from src.llm.config import get_judge_llm_config


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
            manifest["resolved_execution_config"]["selected_variants"],
            ["baseline", "risk", "recovery"],
        )
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

    def test_manifest_records_effective_completion_and_context_limits(self):
        args = argparse.Namespace(
            execution_mode="offline_deterministic",
            run_purpose="dev",
            variant="all",
            repeat_count=1,
            experiment_level="both",
        )

        manifest = build_run_manifest(args, "suite-token-settings", ["CAS-01"])
        tested = manifest["tested_model_identity"]
        judge = manifest["judge_model_identity"]
        judge_config = get_judge_llm_config()

        self.assertEqual(tested["max_completion_tokens"], 16_384)
        self.assertEqual(tested["context_window_tokens"], 128_000)
        self.assertEqual(tested["model_max_completion_tokens"], 16_384)
        self.assertEqual(
            judge["max_completion_tokens"],
            judge_config.judge_max_completion_tokens,
        )
        self.assertEqual(judge["temperature"], judge_config.judge_temperature)
        self.assertEqual(judge["context_window_tokens"], 128_000)
        self.assertEqual(judge["model_max_completion_tokens"], 16_384)
        self.assertNotIn("max_tokens", tested)
        self.assertNotIn("max_tokens", judge)

    def test_manifest_records_recovery_was_intentionally_skipped(self):
        args = argparse.Namespace(
            execution_mode="agentic_live",
            run_purpose="smoke",
            variant="all",
            skip_recovery=True,
            repeat_count=1,
            experiment_level="both",
            order_seed=20260722,
            judge_calibration_report=None,
        )

        manifest = build_run_manifest(args, "suite-risk-focus", ["CAS-01"])

        self.assertEqual(
            manifest["resolved_execution_config"]["selected_variants"],
            ["baseline", "risk"],
        )
        self.assertFalse(
            manifest["resolved_execution_config"]["recovery_executed"]
        )

    def test_run_results_exposes_abort_and_result_counts_at_top_level(self):
        run_outcome = {
            "run_aborted": {
                "case_id": "INC-01",
                "variant": "baseline",
                "failure_code": "INVALID_CONTEXT_OVERFLOW",
                "reason": "Judge 请求超过上下文长度",
            },
            "planned_result_total": 18,
            "completed_result_total": 11,
            "missing_result_total": 7,
        }
        manifest = {
            "manifest_hash": "fixture-manifest",
            **run_outcome,
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "run_results.json"
            save_results(
                [],
                [],
                output,
                "suite-aborted",
                run_purpose="dev",
                execution_mode="offline_deterministic",
                variants=["baseline", "risk"],
                run_manifest=manifest,
            )
            payload = json.loads(output.read_text(encoding="utf-8"))

        self.assertEqual(payload["run_aborted"], run_outcome["run_aborted"])
        self.assertEqual(payload["planned_result_total"], 18)
        self.assertEqual(payload["completed_result_total"], 11)
        self.assertEqual(payload["missing_result_total"], 7)
