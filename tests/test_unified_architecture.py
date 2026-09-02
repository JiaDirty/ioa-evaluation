import hashlib
import json
from pathlib import Path

import pytest

from src.evaluation.business_protocol.loader import load_business_cases
from src.evaluation.candidate_review import discover_candidates
from src.evaluation.scenario_generation.pipeline import sha256_file
from src.evaluation.scenario_generation.unified_architecture import (
    ArtifactRef,
    PipelineOrchestrator,
    ScenarioTask,
    TaskProvenance,
    validate_transition,
    verify_task_hash,
)
from src.evaluation.scenario_generation.legacy_conversion import canonicalize_legacy_case
from src.evaluation.scenario_generation.path_validation import SixPathValidationReport
from src.evaluation.scenario_generation.pipeline_models import EffectSpec, ScenarioKernel
from src.evaluation.scenario_generation.quality_records import (
    HumanDecisionRecord,
    ReviewDimension,
    RuntimeCheckRecord,
    SemanticReviewRecord,
)


ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"
SCENARIOS = ROOT / "data" / "scenarios"


def _candidate_task():
    record = discover_candidates(CANDIDATES)[0]
    return ScenarioTask.from_case(
        record.case,
        task_id="task-" + hashlib.sha256(record.candidate_uid.encode()).hexdigest()[:24],
        provenance=TaskProvenance(
            origin="candidate",
            source_path=record.source_path.as_posix(),
            source_sha256=sha256_file(record.source_path),
            model_id=record.generator_model_id,
        ),
    )


def test_scenario_task_is_the_single_input_envelope():
    task = _candidate_task()
    assert task.schema_version == "scenario_task_v1"
    assert task.provenance.origin == "candidate"
    assert verify_task_hash(task) == task.content_sha256
    assert task.case_payload["case_id"] == task.case_id


def test_registry_transition_is_strict():
    validate_transition(None, "TASK_CREATED")
    validate_transition("TASK_CREATED", "KERNEL_READY")
    with pytest.raises(ValueError, match="invalid canonical transition"):
        validate_transition("TASK_CREATED", "COMPILED")
    with pytest.raises(ValueError, match="invalid canonical transition"):
        validate_transition("EFFECT_READY", "TASK_CREATED")


def test_artifact_refs_reject_absolute_and_traversal_paths():
    for path in ("../outside.json", "cases/../../outside.json", "C:/outside.json"):
        with pytest.raises(ValueError, match="project-relative"):
            ArtifactRef(path=path, sha256="0" * 64, schema_version="test_v1")


def test_migration_task_id_does_not_depend_on_checkout_absolute_path():
    from scripts.migrate_to_unified_tasks import _task_id

    source = ROOT / "data" / "scenarios" / "sample.jsonl"
    expected = "task-" + hashlib.sha256(
        "historical|data/scenarios/sample.jsonl|case-001".encode("utf-8")
    ).hexdigest()[:24]
    assert _task_id("historical", source, "case-001") == expected


def test_orchestrator_writes_one_case_directory_and_single_registry(tmp_path):
    task = _candidate_task()
    orchestrator = PipelineOrchestrator(tmp_path / "unified")
    first = orchestrator.submit(task)
    assert first.stage == "TASK_CREATED"
    processed = orchestrator.process(task.task_id)
    assert processed.stage == "EFFECT_READY"
    assert (tmp_path / "unified" / "registry.json").exists()
    case_dirs = list((tmp_path / "unified" / "cases").iterdir())
    assert len(case_dirs) == 1
    assert {path.name for path in case_dirs[0].iterdir()} >= {
        "scenario_task.json",
        "scenario_kernel.json",
        "effect_spec.json",
        "lineage.json",
    }
    payload = json.loads((tmp_path / "unified" / "registry.json").read_text(encoding="utf-8"))
    assert "entries" in payload and "events" in payload
    assert "pipeline_manifest" not in payload
    refs = payload["entries"][task.task_id]["artifacts"]
    assert all(not ref["path"].startswith(("/", "\\")) for ref in refs.values())
    assert refs["kernel"]["depends_on"] == ["task"]
    assert refs["effect"]["depends_on"] == ["task", "kernel"]
    assert refs["lineage"]["depends_on"] == ["effect", "kernel", "task"]


def test_kernel_change_invalidates_downstream_artifacts(tmp_path):
    task = _candidate_task()
    orchestrator = PipelineOrchestrator(tmp_path / "unified")
    orchestrator.submit(task)
    orchestrator.process(task.task_id)
    entry = orchestrator.registry.get(task.task_id)
    kernel_path = (tmp_path / "unified" / entry.artifacts["kernel"].path)
    payload = json.loads(kernel_path.read_text(encoding="utf-8"))
    payload["title"] += " changed"
    kernel_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="artifact hash mismatch"):
        orchestrator.resume(task.task_id)
    invalidated = orchestrator.registry.get(task.task_id)
    assert invalidated.stage == "INVALIDATED"
    assert set(["kernel", "effect", "compiled"]).issubset(invalidated.invalidated_artifacts)
    resumed = orchestrator.resume(task.task_id)
    assert resumed.stage == "EFFECT_READY"
    assert resumed.generation == 2


def test_historical_case_can_be_wrapped_without_a_legacy_runtime_branch():
    case = next(iter(load_business_cases(SCENARIOS).values()))
    task = ScenarioTask.from_case(
        case,
        task_id="task-" + hashlib.sha256(case.case_id.encode()).hexdigest()[:24],
        provenance=TaskProvenance(origin="historical", source_path="data/scenarios"),
    )
    assert task.category == case.category
    assert task.provenance.origin == "historical"


def test_historical_case_conversion_is_compilable_and_generic():
    case = next(iter(load_business_cases(SCENARIOS).values()))
    converted = canonicalize_legacy_case(case)
    assert converted.scoring_contract is not None
    assert converted.scoring_contract.contract_version == "generic_scoring_v1"
    assert "canonical_evidence" in converted.initial_state["baseline"]


def test_one_registry_controls_post_compile_quality_stages(tmp_path):
    case = next(iter(load_business_cases(SCENARIOS).values()))
    converted = canonicalize_legacy_case(case)
    task = ScenarioTask.from_case(
        converted,
        task_id="task-" + hashlib.sha256((case.case_id + "-post").encode()).hexdigest()[:24],
        provenance=TaskProvenance(origin="historical", source_path="data/scenarios"),
    )
    orchestrator = PipelineOrchestrator(tmp_path / "unified")
    orchestrator.submit(task)
    assert orchestrator.process(task.task_id).stage == "COMPILED"
    report = SixPathValidationReport.model_construct(
        case_id=case.case_id,
        category=case.category,
        status="PASS",
        all_paths_passed=True,
        semantic_paths=[],
        execution_matrix=[],
    )
    assert orchestrator.record_path_validation(task.task_id, report).stage == "PATH_VALID"
    runtime = RuntimeCheckRecord(
        candidate_uid=task.task_id,
        status="PASS",
        runner_version="test",
        summary="offline runtime check passed",
    )
    assert orchestrator.record_runtime_check(task.task_id, runtime).stage == "RUNTIME_VALID"
    reviews = [
        SemanticReviewRecord(
            candidate_uid=task.task_id,
            reviewer_kind="model",
            reviewer_id=f"reviewer-{index}",
            decision="ACCEPT",
            dimensions={"causal": ReviewDimension(passed=True, reason="consistent")},
            confidence=1.0,
        )
        for index in (1, 2)
    ]
    assert orchestrator.record_semantic_reviews(task.task_id, reviews).stage == "SEMANTIC_ACCEPTED"
    decision = HumanDecisionRecord(
        candidate_uid=task.task_id,
        decision="ACCEPT",
        reviewer_id="human",
        reason="approved",
    )
    assert orchestrator.record_human_decision(task.task_id, decision).stage == "HUMAN_ACCEPTED"
    assert orchestrator.freeze(task.task_id).stage == "FROZEN"
    entry = orchestrator.registry.get(task.task_id)
    assert {"path_validation", "runtime_check", "semantic_reviews", "human_decision", "lineage"}.issubset(entry.artifacts)


def test_generated_artifacts_and_retry_use_the_same_registry(tmp_path):
    task = _candidate_task()
    source = PipelineOrchestrator(tmp_path / "source")
    source.submit(task)
    source_entry = source.process(task.task_id)
    kernel = ScenarioKernel.model_validate_json(
        (source.root / source_entry.artifacts["kernel"].path).read_text(encoding="utf-8")
    )
    effect = EffectSpec.model_validate_json(
        (source.root / source_entry.artifacts["effect"].path).read_text(encoding="utf-8")
    )

    target = PipelineOrchestrator(tmp_path / "target")
    target.submit(task)
    assert target.submit_kernel(task.task_id, kernel).stage == "KERNEL_READY"
    assert effect.status == "DRAFT"
    with pytest.raises(ValueError, match="READY_FOR_COMPILE"):
        target.submit_effect(task.task_id, effect)
    assert target.mark_generation_failed(
        task.task_id,
        stage="KERNEL_READY",
        reason="effect needs model revision",
    ).stage == "CHECK_FAILED"
    resumed = target.resume(task.task_id)
    assert resumed.stage == "EFFECT_READY"
    assert resumed.generation == 2
