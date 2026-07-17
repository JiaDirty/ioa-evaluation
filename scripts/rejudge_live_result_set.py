"""Re-run the live Judge over completed live execution bundles."""

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
from src.llm.client import get_judge_llm_client


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _select_reports(source_dirs: list[Path]) -> dict[str, tuple[Path, dict]]:
    selected: dict[str, tuple[Path, dict]] = {}
    for source_dir in source_dirs:
        for path in sorted(source_dir.glob("*.json")):
            data = _load(path)
            scenario_id = data.get("scenario", {}).get("scenario_id")
            if not scenario_id or scenario_id in selected:
                continue
            if not data.get("scenario_evaluation", {}).get("evaluation_valid"):
                continue
            selected[scenario_id] = (path, data)
    return selected


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-dir", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--reuse-existing-live-verdict", action="store_true")
    args = parser.parse_args()

    source_dirs = [Path(value).resolve() for value in args.source_dir]
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    selected = _select_reports(source_dirs)
    if len(selected) != 18:
        raise RuntimeError(f"Expected 18 valid live execution reports, found {len(selected)}")

    client = None if args.reuse_existing_live_verdict else get_judge_llm_client()
    manifest: list[dict] = []
    for index, scenario_id in enumerate(sorted(selected), start=1):
        source_path, report = selected[scenario_id]
        bundle = AttackEvaluationBundle.model_validate(report["attack_evaluation_bundle"])
        output_path = output_dir / f"experiment_report_{scenario_id}.json"
        if output_path.exists():
            completed = _load(output_path)
            completed_verdict = completed.get("judge_verdict")
            if completed.get("scenario_evaluation", {}).get("evaluation_valid") and completed_verdict:
                verdict = JudgeVerdict.model_validate(completed_verdict)
                validator = AttackJudgeAgent()
                validator._enrich_component_attribution(verdict, bundle)
                validator._validate_citations(verdict, bundle)
                validator._validate_live_semantics(verdict, bundle)
                print(f"REJUDGE {index}/18 {scenario_id} SKIP_VALID")
                manifest.append({
                    "scenario_id": scenario_id,
                    "source_execution_report": str(source_path),
                    "final_report": str(output_path),
                    "judge_status": verdict.outcome.status.value,
                    "maximum_stage": verdict.outcome.maximum_stage,
                    "vulnerable_components": verdict.vulnerability.components,
                    "evidence_ids": [item.event_id for item in verdict.evidence],
                })
                continue
        judge = AttackJudgeAgent(
            model_client=client,
            require_live=not args.reuse_existing_live_verdict,
            max_retries=args.max_retries,
        )
        if args.reuse_existing_live_verdict:
            print(f"FINALIZE {index}/18 {scenario_id}")
            verdict = JudgeVerdict.model_validate(report["judge_verdict"])
            judge._validate_citations(verdict, bundle)
            judge._enrich_component_attribution(verdict, bundle)
            judge._validate_live_semantics(verdict, bundle)
        else:
            print(f"REJUDGE {index}/18 {scenario_id}")
            verdict = judge.judge(bundle)

        scenario = ScenarioLoader(report["scenario"]["source"]).load()
        baseline_result = TaskResult.model_validate(report["scenario_runs"]["baseline"])
        attack_result = TaskResult.model_validate(report["scenario_runs"]["attack"])
        attack_bundle = EvaluationEvidenceBundle.model_validate(
            report["evidence_bundles"]["attack"]
        )
        injection = bundle.attack_injection
        attack_context = SimpleNamespace(
            prepared=bool(injection.get("prepared")),
            injection_applied=bool(injection.get("injection_applied")),
        )
        evaluation = _evaluate_scenario_execution(
            scenario,
            baseline_result,
            attack_result,
            attack_bundle,
            bundle,
            verdict,
            attack_context,
        )
        if not evaluation["evaluation_valid"]:
            raise RuntimeError(
                f"Rejudged report is invalid for {scenario_id}: {evaluation['invalid_reasons']}"
            )

        report["judge_verdict"] = verdict.model_dump(mode="json")
        report["scenario_evaluation"] = evaluation
        report["live_judge_replay"] = {
            "source_execution_report": str(source_path),
            "reason": (
                "Existing live Judge verdict retained; empty component attribution was enriched "
                "from immutable attack target logs."
                if args.reuse_existing_live_verdict
                else "Live Judge rerun against the unchanged execution bundle with strict component attribution."
            ),
            "component_attribution_source": "attack_logs",
            "finalized_at": datetime.now().isoformat(),
        }
        _apply_scenario_summary(report, evaluation)
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        manifest.append({
            "scenario_id": scenario_id,
            "source_execution_report": str(source_path),
            "final_report": str(output_path),
            "judge_status": verdict.outcome.status.value,
            "maximum_stage": verdict.outcome.maximum_stage,
            "vulnerable_components": verdict.vulnerability.components,
            "evidence_ids": [item.event_id for item in verdict.evidence],
        })

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now().isoformat(),
                "execution_mode": "agentic_live",
                "report_count": len(manifest),
                "reports": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"LIVE_RESULT_SET={output_dir}")
    print(f"REPORT_COUNT={len(manifest)}")


if __name__ == "__main__":
    main()
