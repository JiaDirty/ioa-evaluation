import argparse
import unittest

from scripts.run_agent_model_suite import (
    build_parser,
    normalize_run_options,
    resolve_variants,
    select_cases,
)
from src.evaluation.agent_model.case_loader import CaseLoader
from scripts.run_agent_model_suite import DATASET


class AgentModelCliTest(unittest.TestCase):
    def test_plan_required_options_parse(self):
        args = normalize_run_options(build_parser().parse_args([
            "--risk", "cascade_propagation",
            "--variant", "risk",
            "--max-cases", "1",
            "--repeat-count", "2",
            "--resume-run-id", "suite-1",
            "--offline-fake-model",
        ]))
        self.assertEqual(args.max_cases, 1)
        self.assertEqual(args.resume_run_id, "suite-1")
        self.assertTrue(args.offline_fake_model)
        self.assertEqual(args.run_purpose, "dev")
        self.assertEqual(args.execution_mode, "offline_deterministic")

    def test_default_run_is_offline_dev(self):
        args = normalize_run_options(build_parser().parse_args([]))
        self.assertEqual(args.run_purpose, "dev")
        self.assertEqual(args.execution_mode, "offline_deterministic")

    def test_smoke_alias_sets_run_purpose(self):
        args = normalize_run_options(build_parser().parse_args(["--smoke"]))
        self.assertEqual(args.run_purpose, "smoke")
        self.assertEqual(args.execution_mode, "offline_deterministic")

    def test_formal_requires_all_variants(self):
        with self.assertRaises(SystemExit):
            normalize_run_options(build_parser().parse_args([
                "--run-purpose", "formal",
                "--variant", "risk",
            ]))

    def test_skip_recovery_selects_baseline_and_risk(self):
        args = normalize_run_options(build_parser().parse_args([
            "--run-purpose", "smoke",
            "--execution-mode", "agentic_live",
            "--skip-recovery",
        ]))

        self.assertEqual(resolve_variants(args), ["baseline", "risk"])

    def test_skip_recovery_conflicts_with_single_variant(self):
        with self.assertRaises(SystemExit):
            normalize_run_options(build_parser().parse_args([
                "--variant", "risk",
                "--skip-recovery",
            ]))

    def test_formal_cannot_skip_recovery(self):
        with self.assertRaises(SystemExit):
            normalize_run_options(build_parser().parse_args([
                "--run-purpose", "formal",
                "--skip-recovery",
            ]))

    def test_formal_requires_live_mode(self):
        with self.assertRaises(SystemExit):
            normalize_run_options(build_parser().parse_args([
                "--run-purpose", "formal",
                "--execution-mode", "offline_deterministic",
            ]))

    def test_formal_requires_both_experiment_levels(self):
        with self.assertRaises(SystemExit):
            normalize_run_options(build_parser().parse_args([
                "--run-purpose", "formal",
                "--experiment-level", "key_node",
            ]))

    def test_offline_alias_conflicts_with_explicit_live(self):
        with self.assertRaises(SystemExit):
            normalize_run_options(build_parser().parse_args([
                "--execution-mode", "agentic_live",
                "--offline-fake-model",
            ]))

    def test_select_cases_applies_risk_and_limit(self):
        cases = CaseLoader(DATASET).load_all()
        args = argparse.Namespace(
            case_id=None,
            smoke=False,
            risk="cascade_propagation",
            max_cases=1,
        )
        selected = select_cases(args, cases)
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].category_code, "CAS")

    def test_smoke_with_explicit_risk_selects_that_category(self):
        cases = CaseLoader(DATASET).load_all()
        args = argparse.Namespace(
            case_id=None,
            smoke=False,
            run_purpose="smoke",
            risk="agency_erosion",
            max_cases=1,
        )
        selected = select_cases(args, cases)
        self.assertEqual([case.category_code for case in selected], ["AGE"])
