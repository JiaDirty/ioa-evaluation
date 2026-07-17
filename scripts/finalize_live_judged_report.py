"""Attach a separately retried live Judge verdict to an existing live run report."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from run_experiment import _apply_scenario_summary, _evaluate_scenario_execution
from src.core.data_models import TaskResult
from src.evaluation import EvaluationEvidenceBundle
from src.evaluation.attack_evaluation_bundle import AttackEvaluationBundle
from src.experiment.scenario_loader import ScenarioLoader
from src.judging import AttackJudgeAgent
from src.judging.schemas import JudgeVerdict


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--verdict", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    report_path = Path(args.report).resolve()
    verdict_path = Path(args.verdict).resolve()
    output_path = Path(args.output).resolve()

    report = json.loads(report_path.read_text(encoding="utf-8"))
    verdict = JudgeVerdict.model_validate_json(verdict_path.read_text(encoding="utf-8"))
    attack_eval_bundle = AttackEvaluationBundle.model_validate(
        report["attack_evaluation_bundle"]
    )

    validator = AttackJudgeAgent()
    validator._validate_citations(verdict, attack_eval_bundle)
    validator._validate_live_semantics(verdict, attack_eval_bundle)

    scenario = ScenarioLoader(report["scenario"]["source"]).load()
    baseline_result = TaskResult.model_validate(report["scenario_runs"]["baseline"])
    attack_result = TaskResult.model_validate(report["scenario_runs"]["attack"])
    attack_bundle = EvaluationEvidenceBundle.model_validate(
        report["evidence_bundles"]["attack"]
    )
    injection = attack_eval_bundle.attack_injection
    attack_context = SimpleNamespace(
        prepared=bool(injection.get("prepared")),
        injection_applied=bool(injection.get("injection_applied")),
    )

    evaluation = _evaluate_scenario_execution(
        scenario,
        baseline_result,
        attack_result,
        attack_bundle,
        attack_eval_bundle,
        verdict,
        attack_context,
    )
    report["judge_verdict"] = verdict.model_dump(mode="json")
    report["scenario_evaluation"] = evaluation
    report["live_judge_replay"] = {
        "source_execution_report": str(report_path),
        "source_live_judge_verdict": str(verdict_path),
        "reason": "Live Judge was retried against the unchanged original AttackEvaluationBundle.",
        "finalized_at": datetime.now().isoformat(),
    }
    _apply_scenario_summary(report, evaluation)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    print(f"FINALIZED_REPORT={output_path}")
    print(f"VALID={evaluation['evaluation_valid']}")
    print(f"JUDGE_STATUS={evaluation['judge_status']}")
    print(f"MAXIMUM_STAGE={evaluation['maximum_stage']}")


if __name__ == "__main__":
    main()
