from pathlib import Path
import unittest

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.dataset_split import (
    dataset_split_hash,
    semantic_duplicate_pairs,
    split_cases,
)
from src.evaluation.agent_model.judge_calibration import (
    calibration_summary,
    validate_blinded_verdict,
)
from src.evaluation.agent_model.metric_contracts import PRIMARY_METRIC_CONTRACTS
from src.evaluation.agent_model.statistics import bootstrap_ci, paired_effect


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


class AgentModelResearchRequirementsTest(unittest.TestCase):
    def test_all_eight_categories_have_primary_metric_contracts(self):
        self.assertEqual(set(PRIMARY_METRIC_CONTRACTS), {
            "CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE",
        })
        for contracts in PRIMARY_METRIC_CONTRACTS.values():
            self.assertTrue(contracts)
            self.assertTrue(all(contract.denominator for contract in contracts))
            self.assertTrue(all(contract.zero_denominator in {"0", "null"} for contract in contracts))
            self.assertTrue(all(
                contract.measurement_layer in {"model_behavior", "final_impact"}
                for contract in contracts
            ))

        self.assertEqual(
            PRIMARY_METRIC_CONTRACTS["JUD"][0].measurement_layer,
            "model_behavior",
        )
        self.assertEqual(
            PRIMARY_METRIC_CONTRACTS["AGE"][0].measurement_layer,
            "final_impact",
        )

    def test_dataset_provenance_does_not_fabricate_a_held_out_split(self):
        cases = CaseLoader(DATASET).load_all()
        self.assertEqual(semantic_duplicate_pairs(cases), [])
        split = split_cases(cases)
        self.assertEqual(len(split["formal_evaluation"]), 160)
        self.assertEqual(len(split["development_exposed"]), 160)
        self.assertEqual(split["held_out"], [])
        self.assertEqual(len(split["controls"]), 4)
        self.assertEqual(dataset_split_hash(split), dataset_split_hash(split_cases(cases)))
        self.assertEqual(
            set(split["formal_evaluation"]),
            set(split["development_exposed"]),
        )

    def test_paired_statistics_and_bootstrap_are_deterministic(self):
        effect = paired_effect([0.1, 0.2], [0.4, 0.5])
        self.assertAlmostEqual(effect["mean_difference"], 0.3)
        self.assertEqual(bootstrap_ci([0.1, 0.2, 0.3]), bootstrap_ci([0.1, 0.2, 0.3]))
        with self.assertRaisesRegex(ValueError, "equal length"):
            paired_effect([1.0], [1.0, 2.0])

    def test_judge_calibration_requires_blinded_evidence_refs(self):
        invalid = {"status": "SAFE_BEHAVIOR", "variant": "risk"}
        self.assertTrue(validate_blinded_verdict(invalid))
        self.assertFalse(calibration_summary([invalid])["calibrated"])
        valid = {"status": "SAFE_BEHAVIOR", "evidence_refs": ["ev-1"]}
        self.assertFalse(validate_blinded_verdict(valid))
