import json
from pathlib import Path

from src.evaluation.candidate_review import discover_candidates
from src.evaluation.scenario_generation.pipeline_runner import ScenarioPipeline
from src.evaluation.scenario_generation.quality_records import (
    HumanDecisionRecord,
    RuntimeCheckRecord,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"


def _pipeline(tmp_path):
    record = discover_candidates(SOURCE)[0]
    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    pipeline.extract([record], audit_records=discover_candidates(SOURCE))
    return pipeline, record.candidate_uid


def test_quality_records_are_persisted_and_failed_runtime_is_not_promoted(tmp_path):
    pipeline, candidate_uid = _pipeline(tmp_path)
    result = pipeline.record_runtime_check(
        RuntimeCheckRecord(
            candidate_uid=candidate_uid,
            status="FAIL",
            runner_version="offline-test",
            summary="工具执行路径失败",
            errors=["recovery did not clear the impact"],
        )
    )

    entry = result.entries[0]
    assert entry.status == "REVISE_REQUIRED"
    runtime_path = Path(entry.stage_paths["runtime_check"])
    assert runtime_path.is_file()
    assert json.loads(runtime_path.read_text(encoding="utf-8"))["status"] == "FAIL"


def test_quality_gates_can_advance_without_creating_live_clients(tmp_path):
    pipeline, candidate_uid = _pipeline(tmp_path)
    manifest = pipeline._load_manifest()
    assert manifest is not None
    manifest.entries[0].status = "SIX_PATH_VALID"
    pipeline._save_manifest(manifest)

    pipeline.record_runtime_check(
        {
            "candidate_uid": candidate_uid,
            "status": "PASS",
            "runner_version": "offline-test",
            "summary": "离线运行通过",
        }
    )
    pipeline.record_semantic_review(
        {
            "candidate_uid": candidate_uid,
            "reviewer_kind": "external",
            "reviewer_id": "reviewer-1",
            "decision": "ACCEPT",
            "dimensions": {
                "causal": {
                    "passed": True,
                    "reason": "因果对照清晰",
                }
            },
            "confidence": 0.9,
        }
    )
    result = pipeline.record_human_decision(
        HumanDecisionRecord(
            candidate_uid=candidate_uid,
            decision="ACCEPT",
            reviewer_id="human-1",
            reason="终审通过",
            release_membership=["formal_v1"],
        )
    )

    entry = result.entries[0]
    assert entry.status == "FORMAL_ACCEPTED"
    assert result.summary["runtime_check_count"] == 1
    assert result.summary["semantic_review_count"] == 1
    assert result.summary["human_decision_count"] == 1
    assert len(list((tmp_path / "pipeline" / "accepted").glob("*.json"))) == 1
