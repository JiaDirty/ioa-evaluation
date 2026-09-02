from __future__ import annotations

import inspect
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.migrations.build_tasks import build_all_tasks
from src.evaluation.scenario_generation.artifact_store import ArtifactStore
from src.evaluation.scenario_generation.catalog import load_evaluation_catalog
from src.evaluation.scenario_generation.models import (
    ArtifactRef,
    PipelineStage,
    ScenarioTask,
    TaskProvenance,
    validate_transition,
)
from src.evaluation.scenario_generation.orchestrator import PipelineOrchestrator
from src.evaluation.scenario_generation.registry import PipelineRegistry, stage_for_artifacts


ROOT = Path(__file__).resolve().parents[1]


def _task(*, origin: str = "manual", task_id: str = "task-0123456789ab") -> ScenarioTask:
    return ScenarioTask.create(
        task_id=task_id,
        branch_id="cas",
        objective="验证范围错误是否被角色链传播并触发错误执行。",
        mechanism_requirements=["范围错误的信息沿角色链传播"],
        provenance=TaskProvenance(origin=origin),
    )


def _ref(store: ArtifactStore, path: str, value: str = "x") -> ArtifactRef:
    return store.write_text(path, value)


def test_lightweight_task_has_no_complete_case_field():
    task = _task()
    assert not hasattr(task, "case")
    assert task.reference_material == []


def test_task_hash_is_sealed_and_tampering_is_detected():
    task = _task()
    assert task.content_sha256
    tampered = task.model_copy(update={"objective": "被篡改后的目标"})
    with pytest.raises(ValueError, match="hash mismatch"):
        from src.evaluation.scenario_generation.models import verify_task_hash

        verify_task_hash(tampered)


def test_catalog_defines_eleven_leaf_branches_and_fifty_five_quota():
    catalog = load_evaluation_catalog()
    assert len(catalog.branch_ids) == 11
    assert catalog.total_release_quota == 55
    assert all(quota == 5 for quota in catalog.release_quota.values())


def test_registry_is_single_durable_status_center(tmp_path):
    registry = PipelineRegistry(tmp_path)
    entry = registry.register("task-0123456789ab", "case-1")
    assert registry.path == tmp_path.resolve() / "registry.json"
    assert registry.get(entry.task_id).stage == "TASK_READY"
    assert len(registry.data.events) == 1
    assert json.loads(registry.path.read_text(encoding="utf-8"))["entries"]


def test_registry_registration_is_idempotent(tmp_path):
    registry = PipelineRegistry(tmp_path)
    first = registry.register("task-0123456789ab", "case-1")
    second = registry.register("task-0123456789ab", "case-1")
    assert first.task_id == second.task_id
    assert len(registry.data.events) == 1


def test_strict_state_machine_rejects_skipping_effect_stage():
    with pytest.raises(ValueError, match="invalid pipeline transition"):
        validate_transition("TASK_READY", "COMPILED")


def test_draft_to_ready_to_compiled_transitions_are_explicit():
    validate_transition("KERNEL_READY", "EFFECT_DRAFT")
    validate_transition("EFFECT_DRAFT", "EFFECT_READY")
    validate_transition("EFFECT_READY", "COMPILED")


def test_needs_revision_can_reenter_draft_and_ready():
    validate_transition("EFFECT_NEEDS_REVISION", "EFFECT_DRAFT")
    validate_transition("EFFECT_NEEDS_REVISION", "EFFECT_READY")


def test_kernel_invalidation_preserves_task_only(tmp_path):
    registry = PipelineRegistry(tmp_path)
    store = ArtifactStore(tmp_path)
    registry.register("task-0123456789ab", "case-1")
    registry.transition("task-0123456789ab", "KERNEL_READY", reason="kernel ready", artifacts={"task": _ref(store, "task.txt"), "kernel": _ref(store, "kernel.txt")})
    registry.add_artifact("task-0123456789ab", "effect", _ref(store, "effect.txt"))
    registry.add_artifact("task-0123456789ab", "compiled", _ref(store, "compiled.txt"))
    result = registry.invalidate_artifact("task-0123456789ab", "kernel", reason="kernel changed")
    assert set(result.artifacts) == {"task"}
    assert result.stage == "TASK_READY"
    assert "kernel" in result.invalidated_artifacts


def test_effect_invalidation_preserves_kernel(tmp_path):
    registry = PipelineRegistry(tmp_path)
    store = ArtifactStore(tmp_path)
    registry.register("task-0123456789ab", "case-1")
    registry.transition("task-0123456789ab", "KERNEL_READY", reason="kernel ready", artifacts={"task": _ref(store, "task.txt"), "kernel": _ref(store, "kernel.txt")})
    registry.add_artifact("task-0123456789ab", "effect", _ref(store, "effect.txt"))
    registry.add_artifact("task-0123456789ab", "compiled", _ref(store, "compiled.txt"))
    result = registry.invalidate_artifact("task-0123456789ab", "effect", reason="effect changed")
    assert set(result.artifacts) == {"task", "kernel"}
    assert result.stage == "KERNEL_READY"
    assert "compiled" in result.invalidated_artifacts


def test_stage_for_artifacts_requires_the_full_dependency_chain(tmp_path):
    store = ArtifactStore(tmp_path)
    assert stage_for_artifacts({}) == "TASK_READY"
    assert stage_for_artifacts({"kernel": _ref(store, "kernel.txt")}) == "KERNEL_READY"
    assert stage_for_artifacts({"kernel": _ref(store, "kernel.txt"), "effect": _ref(store, "effect.txt")}) == "EFFECT_READY"
    assert stage_for_artifacts({"kernel": _ref(store, "kernel.txt"), "effect": _ref(store, "effect.txt"), "compiled": _ref(store, "compiled.txt")}) == "COMPILED"


def test_resume_from_generation_failure_reenters_real_flow(tmp_path):
    orchestrator = PipelineOrchestrator(tmp_path, raw_root=tmp_path / "raw")
    task = _task()
    orchestrator.submit(task)
    orchestrator.registry.transition(task.task_id, "GENERATION_FAILED", reason="provider unavailable")
    result = orchestrator.resume(task.task_id)
    assert result.stage == "GENERATION_FAILED"
    assert result.notes
    assert len(orchestrator.registry.data.events) >= 3


def test_reference_and_candidate_tasks_use_the_same_submit_method(tmp_path):
    orchestrator = PipelineOrchestrator(tmp_path, raw_root=tmp_path / "raw")
    reference = _task(origin="reference", task_id="task-0123456789ab")
    candidate = _task(origin="candidate", task_id="task-0123456789ac")
    assert orchestrator.submit(reference).stage == "TASK_READY"
    assert orchestrator.submit(candidate).stage == "TASK_READY"
    assert {entry.stage for entry in orchestrator.registry.entries().values()} == {"TASK_READY"}


def test_all_sources_build_to_one_task_collection():
    tasks = build_all_tasks(
        reference_dir=ROOT / "data/raw/reference_sources",
        candidate_dir=ROOT / "data/raw/candidate_sources/批量生成-第01轮",
        raw_root=ROOT / "data/raw",
    )
    assert len(tasks) == 451
    assert {task.provenance.origin for task in tasks} == {"reference", "candidate"}
    assert len({task.task_id for task in tasks}) == 451


def test_reference_compiled_cases_have_bound_hashes_and_oracles():
    registry = json.loads((ROOT / "data/workspace/registry.json").read_text(encoding="utf-8"))
    references = [entry for entry in registry["entries"].values() if entry.get("stage") == "RUNTIME_VALID"]
    assert len(references) == 11
    for entry in references:
        assert {"task", "kernel", "effect", "compiled"}.issubset(entry["artifacts"])
        effect = json.loads((ROOT / "data/workspace" / entry["artifacts"]["effect"]["path"]).read_text(encoding="utf-8"))
        assert effect["status"] == "READY_FOR_COMPILE"
        assert all((not step["scored"]) or step.get("behavior") for step in effect["steps"])


def test_reference_compiled_case_is_not_a_source_copy():
    registry = json.loads((ROOT / "data/workspace/registry.json").read_text(encoding="utf-8"))
    for entry in registry["entries"].values():
        if entry.get("stage") != "RUNTIME_VALID":
            continue
        compiled = json.loads((ROOT / "data/workspace" / entry["artifacts"]["compiled"]["path"]).read_text(encoding="utf-8"))
        assert compiled["metadata"] if "metadata" in compiled else True
        assert compiled["case"]["metadata"].get("reference_conversion") == "reference_to_generic_v1"


def test_compiler_has_no_source_case_assignment_bypass():
    from src.evaluation.scenario_generation import compiler

    source = inspect.getsource(compiler.compile_kernel_effect)
    assert "compiled_case = source_case" not in source
    assert "return source_case" not in source


def test_cli_reports_partial_when_blocked_and_never_false_completion():
    completed = subprocess.run(
        [sys.executable, "scripts/run_pipeline.py", "process", "--all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    payload = json.loads(completed.stdout)
    assert payload["status"] != "PIPELINE_COMPLETED"
    assert payload["status"] == "PIPELINE_PARTIAL"
    assert completed.returncode == 3
