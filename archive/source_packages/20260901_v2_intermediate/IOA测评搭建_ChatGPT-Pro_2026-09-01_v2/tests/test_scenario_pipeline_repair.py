import json
from pathlib import Path

import pytest

from src.evaluation.candidate_review import discover_candidates
from src.evaluation.scenario_generation.pipeline_runner import ScenarioPipeline
from src.evaluation.scenario_generation.repair import (
    apply_effect_repair,
    build_repair_plan,
    extract_effect_draft_payload,
    render_repair_prompt,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"


def test_repair_queue_covers_selected_candidates_without_touching_source(tmp_path):
    records = discover_candidates(SOURCE)
    selected = ScenarioPipeline.select(records, sample_per_item=1)
    source_bytes = {record.source_path: record.source_path.read_bytes() for record in selected}
    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    pipeline.extract(selected, audit_records=records)
    manifest = pipeline.prepare_repairs()

    assert len(manifest.entries) == 11
    assert all(entry.repair_status == "PENDING" for entry in manifest.entries)
    assert all(entry.stage_paths.get("repair_plan") for entry in manifest.entries)
    assert all(entry.stage_paths.get("repair_prompt") for entry in manifest.entries)
    assert all(entry.stage_paths.get("repair_result") for entry in manifest.entries)
    assert (tmp_path / "pipeline" / "repair_queue.jsonl").read_text(encoding="utf-8").count("\n") == 11
    assert all(path.read_bytes() == content for path, content in source_bytes.items())


def test_repair_plan_requires_semantic_contract_and_keeps_hashes(tmp_path):
    record = discover_candidates(SOURCE)[0]
    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    manifest = pipeline.extract([record], audit_records=discover_candidates(SOURCE))
    entry = manifest.entries[0]
    kernel = json.loads(Path(entry.stage_paths["kernel"]).read_text(encoding="utf-8"))
    effect = json.loads(Path(entry.stage_paths["effect_spec"]).read_text(encoding="utf-8"))
    from src.evaluation.scenario_generation.pipeline_models import EffectSpec, ScenarioKernel

    plan = build_repair_plan(
        entry,
        ScenarioKernel.model_validate(kernel),
        EffectSpec.model_validate(effect),
        json.loads(Path(entry.stage_paths["kernel_review"]).read_text(encoding="utf-8")),
    )
    assert plan.kernel_sha256 == entry.kernel_sha256
    assert plan.effect_sha256 == entry.effect_sha256
    assert any(item.operation_id == "repair-scoring-contract" for item in plan.required_operations)
    prompt = render_repair_prompt(
        ScenarioKernel.model_validate(kernel), EffectSpec.model_validate(effect), plan
    )
    assert "kernel_sha256" in prompt
    assert "只输出一个 effect_spec_draft_v1 JSON 对象" in prompt


def test_repair_response_wrapper_is_strictly_unwrapped():
    payload = {
        "effect_spec_draft": {
            "schema_version": "effect_spec_draft_v1",
            "kernel_id": "kernel-test-1234",
            "kernel_sha256": "0" * 64,
            "steps": [],
            "safe_final_states": [],
            "unsafe_final_states": [],
            "execution_plan": {},
        }
    }
    assert extract_effect_draft_payload(payload)["schema_version"] == "effect_spec_draft_v1"
    with pytest.raises(ValueError, match="does not contain"):
        extract_effect_draft_payload({"schema_version": "effect_spec_v1"})


def test_repair_response_cannot_change_kernel_hash(tmp_path):
    record = discover_candidates(SOURCE)[0]
    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    manifest = pipeline.extract([record], audit_records=discover_candidates(SOURCE))
    entry = manifest.entries[0]
    from src.evaluation.scenario_generation.pipeline_models import ScenarioKernel, EffectSpec

    kernel = ScenarioKernel.model_validate_json(
        Path(entry.stage_paths["kernel"]).read_text(encoding="utf-8")
    )
    effect = EffectSpec.model_validate_json(
        Path(entry.stage_paths["effect_spec"]).read_text(encoding="utf-8")
    )
    bad = {
        "schema_version": "effect_spec_draft_v1",
        "kernel_id": kernel.kernel_id,
        "kernel_sha256": "f" * 64,
        "steps": [],
        "safe_final_states": [],
        "unsafe_final_states": [],
        "execution_plan": {},
    }
    with pytest.raises(ValueError):
        apply_effect_repair(bad, kernel=kernel)
    assert effect.status == "DRAFT"
