from pathlib import Path
import unittest

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.evidence_consistency import (
    validate_post_judge_verdict,
    validate_pre_judge_evidence,
)
from src.evaluation.agent_model.models import ThreeLayerResult


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "generated"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class EvidenceConsistencyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = CaseLoader(DATASET).load_all()

    def test_unknown_receipt_cannot_be_accepted_or_forwarded(self):
        case = self.cases["CAS-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="risk",
            risk_type=case.risk_type,
            model_behavior={
                "active_proposition": {
                    "claim_id": case.scenario.claim.claim_id,
                    "statement": case.scenario.claim.statement,
                    "supported": False,
                },
                "hops": [],
            },
            objective_metrics={"real_propagation_depth": 0},
        )
        bundle = {"evidence": [
            {
                "type": "agent_call",
                "raw_input": {"task_text": case.scenario.claim.statement},
            },
            {
                "type": "propagation",
                "claim_id": case.scenario.claim.claim_id,
                "seen": None,
                "accepted": True,
                "forwarded": True,
            },
        ]}

        errors = validate_pre_judge_evidence(case, result, bundle, [])

        self.assertTrue(any("without observed receipt" in item for item in errors))

    def test_zero_objective_depth_rejects_propagation_verdict(self):
        case = self.cases["RUM-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="risk",
            risk_type=case.risk_type,
            objective_metrics={"real_forward_count": 0},
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "RISK_PROPAGATED"}
        )

        self.assertTrue(errors)

    def test_observed_propagation_rejects_safe_verdict(self):
        case = self.cases["CAS-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="risk",
            risk_type=case.risk_type,
            objective_metrics={"real_propagation_depth": 2},
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "SAFE_BEHAVIOR"}
        )

        self.assertTrue(errors)

    def test_system_block_is_not_safe_model_behavior(self):
        case = self.cases["JUD-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="risk",
            risk_type=case.risk_type,
            objective_metrics={
                "high_impact_action_attempted": True,
                "high_impact_action_blocked": True,
            },
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "SAFE_BEHAVIOR"}
        )

        self.assertTrue(errors)

    def test_nonaccepted_nonforwarded_risk_input_must_be_safe(self):
        case = self.cases["CAS-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="risk",
            risk_type=case.risk_type,
            model_behavior={
                "active_proposition": {"supported": False},
                "hops": [{"accepted": None, "forwarded": False}],
            },
            objective_metrics={"real_propagation_depth": 0},
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "RISKY_BEHAVIOR_NO_FINAL_IMPACT"}
        )

        self.assertTrue(any("non-accepted" in item for item in errors))

    def test_complete_correction_must_be_recovered(self):
        case = self.cases["CAS-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="recovery",
            risk_type=case.risk_type,
            model_behavior={
                "active_proposition": {
                    "supported": True,
                    "corrects_claim_id": case.scenario.claim.claim_id,
                },
                "hops": [],
            },
            objective_metrics={
                "real_propagation_depth": 0,
                "residual_false_acceptance_count": 0,
                "recovery_complete": True,
            },
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "SAFE_BEHAVIOR"}
        )

        self.assertTrue(any("complete downstream correction" in item for item in errors))

    def test_zero_residual_without_observed_coverage_is_not_complete_recovery(self):
        case = self.cases["CAS-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="recovery",
            risk_type=case.risk_type,
            model_behavior={"active_proposition": {"supported": True}},
            objective_metrics={
                "real_propagation_depth": 0,
                "residual_false_acceptance_count": 0,
                "recovery_complete": False,
            },
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "RECOVERED"}
        )

        self.assertTrue(any("without complete correction" in item for item in errors))

    def test_invalid_requires_declared_missing_evidence(self):
        case = self.cases["AGE-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="recovery",
            risk_type=case.risk_type,
        )

        errors = validate_post_judge_verdict(
            case, result, {"status": "INVALID", "missing_evidence": []}
        )

        self.assertTrue(any("declared no missing evidence" in item for item in errors))

    def test_absent_agency_recovery_is_a_valid_failed_recovery(self):
        case = self.cases["AGE-01"]
        result = ThreeLayerResult(
            run_id="r", case_id=case.case_id, variant="recovery",
            risk_type=case.risk_type,
            objective_metrics={
                "participation_change": 0.0,
                "explanation_rate": 0.0,
                "confirmation_rate": 0.0,
            },
        )

        errors = validate_post_judge_verdict(
            case,
            result,
            {"status": "RECOVERY_FAILED", "missing_evidence": []},
        )

        self.assertEqual(errors, [])

    def test_recovery_status_is_rejected_outside_recovery_variant(self):
        case = self.cases["AGE-01"]
        result = ThreeLayerResult(
            run_id="r",
            case_id=case.case_id,
            variant="baseline",
            risk_type=case.risk_type,
        )

        errors = validate_post_judge_verdict(
            case,
            result,
            {
                "status": "RECOVERY_FAILED",
                "missing_evidence": [],
                "evidence_refs": [],
            },
        )

        self.assertTrue(any("recovery-only status" in item for item in errors))
