import hashlib
import json
import unittest

from src.evaluation.agent_model.controls import run_control_checks
from src.evaluation.agent_model.formal_guard import (
    FormalRunConfig,
    FormalRunGuardError,
    validate_formal_coverage,
    validate_formal_run,
)
from src.evaluation.agent_model.judge_calibration import calibration_set_hash
from src.evaluation.agent_model.models import PairedRunResult, ThreeLayerResult


JUDGE_IDENTITY = {
    "provider": "provider-b", "model": "judge-model", "endpoint_hash": "judge-endpoint",
}
RAW_LABELS = [
    {
        "item_id": f"cal-{index:02d}",
        "human_rater_a_id": "human-a",
        "human_rater_b_id": "human-b",
        "human_rater_a_status": (
            "SAFE_BEHAVIOR" if index % 2 else "RISK_PROPAGATED"
        ),
        "human_rater_b_status": (
            "SAFE_BEHAVIOR" if index % 2 else "RISK_PROPAGATED"
        ),
        "gold_status": "SAFE_BEHAVIOR" if index % 2 else "RISK_PROPAGATED",
        "judge_status": "SAFE_BEHAVIOR" if index % 2 else "RISK_PROPAGATED",
        "blinded_input_hash": f"input-{index:02d}",
    }
    for index in range(20)
]

MANIFEST = {
    "git_commit": "g",
    "dirty_diff_hash": "dd",
    "dataset_hash": "d",
    "case_hashes": {"CAS-01": "h"},
    "code_hash": "c",
    "package_lock_hash": "l",
    "environment": {"python": "3"},
    "model_config_hash": "m",
    "tool_manifest_hash": "t",
    "prompt_hash": "pr",
    "fixture_policy_hash": "f",
    "topology_hash": "p",
    "resolved_execution_config": {"execution_mode": "agentic_live"},
    "dataset_split_hash": "s",
    "dataset_split": {
        "development_exposed": ["CAS-01"],
        "formal_evaluation": ["CAS-01"],
        "held_out": [],
        "controls": [],
    },
    "dataset_provenance": {
        "dataset_role": "development_exposed_preregistered_evaluation",
        "all_cases_seen_during_evaluator_development": True,
        "held_out_case_count": 0,
    },
    "planned_order": ["CAS-01"],
    "formal_eligibility_rules_version": "v1",
    "formal_plan": {
        "case_ids": ["CAS-01"],
        "repeat_count_by_case": {"CAS-01": 1},
        "experiment_levels_by_case": {"CAS-01": ["key_node"]},
        "variants": ["baseline", "risk", "recovery"],
    },
    "formal_plan_hash": "formal-plan",
    "tested_model_identity": {
        "provider": "provider-a", "model": "tested-model", "endpoint_hash": "tested-endpoint",
    },
    "judge_model_identity": JUDGE_IDENTITY,
    "judge_calibration": {
        "calibrated": True,
        "blinded": True,
        "independent_from_tested_model": True,
        "raw_labels": RAW_LABELS,
        "cohen_kappa": 1.0,
        "human_cohen_kappa": 1.0,
        "judge_gold_cohen_kappa": 1.0,
        "calibration_set_hash": calibration_set_hash(RAW_LABELS),
        "rater_profiles": [
            {
                "rater_id": "human-a", "rater_type": "human",
                "independent": True, "blinded": True,
            },
            {
                "rater_id": "human-b", "rater_type": "human",
                "independent": True, "blinded": True,
            },
        ],
        "blinding_audit": {
            "performed": True,
            "violations": [],
            "calibration_input_hash": "calibration-input-hash",
        },
        "judge_model_identity": JUDGE_IDENTITY,
    },
    "control_results": run_control_checks(),
}
MANIFEST["formal_plan_hash"] = hashlib.sha256(
    json.dumps(
        MANIFEST["formal_plan"], ensure_ascii=False, sort_keys=True
    ).encode("utf-8")
).hexdigest()


class AgentModelFormalGuardTest(unittest.TestCase):
    def test_dev_runs_are_not_blocked(self):
        validate_formal_run(FormalRunConfig(
            run_purpose="dev",
            execution_mode="offline_deterministic",
            variants=["risk"],
            judge_configured=False,
            fake_model=True,
        ))

    def test_formal_rejects_offline_or_fake(self):
        with self.assertRaises(FormalRunGuardError):
            validate_formal_run(FormalRunConfig(
                run_purpose="formal",
                execution_mode="offline_deterministic",
                variants=["baseline", "risk", "recovery"],
                judge_configured=True,
                fake_model=True,
                manifest=MANIFEST,
            ))

    def test_formal_rejects_missing_manifest(self):
        with self.assertRaises(FormalRunGuardError):
            validate_formal_run(FormalRunConfig(
                run_purpose="formal",
                execution_mode="agentic_live",
                variants=["baseline", "risk", "recovery"],
                judge_configured=True,
                fake_model=False,
                manifest={},
            ))

    def test_formal_rejects_same_tested_and_judge_model(self):
        manifest = dict(MANIFEST)
        manifest["judge_model_identity"] = dict(
            MANIFEST["tested_model_identity"]
        )
        manifest["judge_calibration"] = {
            **MANIFEST["judge_calibration"],
            "judge_model_identity": dict(MANIFEST["tested_model_identity"]),
        }
        with self.assertRaisesRegex(FormalRunGuardError, "independent"):
            validate_formal_run(FormalRunConfig(
                run_purpose="formal",
                execution_mode="agentic_live",
                variants=["baseline", "risk", "recovery"],
                judge_configured=True,
                manifest=manifest,
            ))

    def test_formal_rejects_incomplete_case_or_repeat_coverage(self):
        results = [
            ThreeLayerResult(
                run_id=f"run-{variant}", case_id="CAS-01", variant=variant,
                risk_type="cascade_propagation", experiment_level="key_node",
            )
            for variant in ("baseline", "risk")
        ]
        pair = PairedRunResult(
            paired_unit_id="pair-1", case_id="CAS-01", repeat_index=0,
            experiment_level="key_node",
            baseline_run_id="run-baseline", risk_run_id="run-risk",
            baseline_scenario_state_id="baseline-state",
            risk_scenario_state_id="risk-state",
        )
        manifest = {
            **MANIFEST,
            "actual_order": [
                {
                    "case_id": item.case_id,
                    "variant": item.variant,
                    "experiment_level": item.experiment_level,
                }
                for item in results
            ],
        }

        errors = validate_formal_coverage(results, [pair], manifest)

        self.assertTrue(any("run coverage" in error for error in errors))
        self.assertTrue(any("actual run order" in error for error in errors))

    def test_formal_rejects_a_post_hoc_partial_case_plan(self):
        manifest = {
            **MANIFEST,
            "dataset_split": {
                **MANIFEST["dataset_split"],
                "formal_evaluation": ["CAS-01", "CAS-02"],
                "development_exposed": ["CAS-01", "CAS-02"],
            },
        }

        with self.assertRaisesRegex(FormalRunGuardError, "complete registered"):
            validate_formal_run(FormalRunConfig(
                run_purpose="formal",
                execution_mode="agentic_live",
                variants=["baseline", "risk", "recovery"],
                judge_configured=True,
                manifest=manifest,
            ))

    def test_formal_rejects_tampered_plan_contents(self):
        manifest = {
            **MANIFEST,
            "formal_plan": {
                **MANIFEST["formal_plan"],
                "repeat_count_by_case": {"CAS-01": 2},
            },
        }
        with self.assertRaisesRegex(FormalRunGuardError, "plan hash"):
            validate_formal_run(FormalRunConfig(
                run_purpose="formal",
                execution_mode="agentic_live",
                variants=["baseline", "risk", "recovery"],
                judge_configured=True,
                manifest=manifest,
            ))
