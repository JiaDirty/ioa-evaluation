import json
from pathlib import Path
from types import SimpleNamespace

from src.evaluation.candidate_review import discover_candidates
from src.evaluation.scenario_generation.pipeline import (
    extract_effect_spec,
    extract_scenario_kernel,
    sha256_file,
)
from src.evaluation.scenario_generation.pipeline_models import (
    EffectSpec,
    seal_effect_spec,
)
from src.evaluation.scenario_generation.pipeline_runner import ScenarioPipeline


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"


def test_selection_is_stable_and_covers_each_evaluation_item():
    records = discover_candidates(SOURCE)
    first = ScenarioPipeline.select(records, sample_per_item=2)
    second = ScenarioPipeline.select(records, sample_per_item=2)
    assert [item.candidate_uid for item in first] == [item.candidate_uid for item in second]
    assert len(first) == 22
    from collections import Counter

    assert set(Counter(item.item_name for item in first).values()) == {2}


def test_extract_is_idempotent_and_keeps_source_unchanged(tmp_path):
    records = discover_candidates(SOURCE)
    selected = ScenarioPipeline.select(records, sample_per_item=1)
    source_hashes = {item.source_path: item.source_path.read_bytes() for item in selected}
    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    first = pipeline.extract(selected, audit_records=records)
    manifest_path = tmp_path / "pipeline" / "pipeline_manifest.json"
    first_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    attempts = {
        item["candidate_uid"]: item["attempts"].get("extract")
        for item in first_payload["entries"]
    }
    second = pipeline.extract(selected, audit_records=records)
    second_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert len(first.entries) == 11
    assert len(second.entries) == 11
    assert {
        item["candidate_uid"]: item["attempts"].get("extract")
        for item in second_payload["entries"]
    } == attempts
    assert all(path.read_bytes() == content for path, content in source_hashes.items())
    assert all(item.kernel_id and item.effect_id for item in second.entries)


def test_manifest_preserves_explicit_source_kind(tmp_path):
    records = discover_candidates(SOURCE)
    selected = ScenarioPipeline.select(records, sample_per_item=1)
    pipeline = ScenarioPipeline(
        SOURCE,
        tmp_path / "generated-pipeline",
        source_kind="generated",
    )
    manifest = pipeline.extract(selected, audit_records=records)
    assert manifest.entries
    assert {entry.source_kind for entry in manifest.entries} == {"generated"}


def test_compile_stage_leaves_legacy_drafts_pending(tmp_path):
    records = discover_candidates(SOURCE)
    selected = ScenarioPipeline.select(records, sample_per_item=1)
    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    extracted = pipeline.extract(selected, audit_records=records)
    compiled = pipeline.compile_ready()
    assert all(item.effect_status == "DRAFT" for item in compiled.entries)
    assert all(item.status == extracted_item.status for item, extracted_item in zip(compiled.entries, extracted.entries))
    assert compiled.summary["compiled_count"] == 0


def test_compile_stage_promotes_only_after_six_path_gate(tmp_path, monkeypatch):
    """A ready effect may compile, but only a passing independent report promotes it."""

    records = discover_candidates(SOURCE)
    # Pick a legacy candidate whose extracted tools are already representable in
    # the authoring format.  The test still supplies its own deliberately
    # minimal behaviour/terminal contract and mocks only the compiler and path
    # evaluator, so this remains an integration test of the runner gate.
    chosen = None
    for record in records:
        kernel = extract_scenario_kernel(
            record,
            source_sha256=sha256_file(record.source_path),
        )
        effect = extract_effect_spec(record, kernel)
        if all(tool.legacy_condition_effects is None for step in effect.steps for tool in step.tools):
            chosen = (record, kernel, effect)
            break
    assert chosen is not None
    record, _kernel, draft = chosen

    pipeline = ScenarioPipeline(SOURCE, tmp_path / "pipeline")
    pipeline.extract([record], audit_records=records)
    manifest = pipeline._load_manifest()
    assert manifest is not None
    entry = manifest.entries[0]

    payload = draft.model_dump(mode="json")
    payload["status"] = "READY_FOR_COMPILE"
    payload["safe_final_states"] = [
        {
            "match": "all",
            "conditions": ["normal", "risk", "recovery"],
            "criteria": [
                {"field_path": "pipeline_gate", "operator": "equals", "expected": "safe"}
            ],
        }
    ]
    payload["unsafe_final_states"] = [
        {
            "match": "all",
            "conditions": ["normal", "risk", "recovery"],
            "criteria": [
                {"field_path": "pipeline_gate", "operator": "equals", "expected": "unsafe"}
            ],
        }
    ]
    for step in payload["steps"]:
        step["scored"] = False
        step["objective_unsafe_tools"] = []
        for tool in step["tools"]:
            tool.pop("legacy_condition_effects", None)
    ready_effect = seal_effect_spec(EffectSpec.model_validate(payload))
    effect_path = Path(entry.stage_paths["effect_spec"])
    effect_path.write_text(ready_effect.model_dump_json(indent=2) + "\n", encoding="utf-8")
    entry.effect_id = ready_effect.effect_id
    entry.effect_sha256 = ready_effect.content_sha256
    entry.effect_status = ready_effect.status
    pipeline._save_manifest(manifest)

    import src.evaluation.scenario_generation.pipeline_runner as runner_module

    class FakeCompiled:
        def model_dump(self, mode="json"):
            return {"case_id": "compiled-test", "mode": mode}

    class FakeReport:
        def __init__(self, status, errors=None):
            self.status = status
            self.errors = list(errors or [])

        def model_dump(self, mode="json"):
            return {"status": self.status, "errors": self.errors, "mode": mode}

    monkeypatch.setattr(runner_module, "compile_kernel_effect", lambda *args, **kwargs: FakeCompiled())
    monkeypatch.setattr(runner_module, "oracle_from_effect", lambda effect: SimpleNamespace())
    monkeypatch.setattr(
        runner_module,
        "validate_six_paths",
        lambda case, oracle: FakeReport("PASS"),
    )
    passed = pipeline.compile_ready()
    assert passed.entries[0].status == "SIX_PATH_VALID", [
        error.model_dump(mode="json") for error in passed.entries[0].errors
    ]
    validation_path = Path(passed.entries[0].stage_paths["validation"])
    assert json.loads(validation_path.read_text(encoding="utf-8"))["status"] == "PASS"

    monkeypatch.setattr(
        runner_module,
        "validate_six_paths",
        lambda case, oracle: FakeReport("FAIL", ["synthetic path mismatch"]),
    )
    failed = pipeline.compile_ready(force=True)
    assert failed.entries[0].status == "REVISE_REQUIRED"
    assert any(
        error.code == "SIX_PATH_VALIDATION_FAILED"
        for error in failed.entries[0].errors
    )
