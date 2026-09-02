import json
from pathlib import Path

import pytest

from src.evaluation.candidate_review import discover_candidates
from src.evaluation.scenario_generation.pipeline import extract_scenario_kernel, sha256_file
from src.evaluation.scenario_generation.pipeline_models import PipelineManifestEntry
from src.evaluation.scenario_generation.unified_pipeline import (
    CandidateRegistryEntry,
    TaskCard,
    build_task_card,
    is_valid_transition,
    validate_transition,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"


def _entry(record):
    return PipelineManifestEntry(
        candidate_uid=record.candidate_uid,
        source_case_id=record.case.case_id,
        source_path=str(record.source_path.resolve()),
        source_sha256=sha256_file(record.source_path),
        source_hash_verified=True,
        category=record.case.category,
        evaluation_item=record.item_name,
        generator_model_id=record.generator_model_id,
        batch_id=record.batch_id,
        updated_at="2026-01-01T00:00:00+00:00",
    )


def test_task_card_is_program_bound_to_catalog_and_stable():
    record = discover_candidates(SOURCE)[0]
    entry = _entry(record)
    kernel = extract_scenario_kernel(record, source_sha256=entry.source_sha256)
    first = build_task_card(entry, kernel)
    second = build_task_card(entry, kernel)
    assert first.model_dump(mode="json") == second.model_dump(mode="json")
    assert first.category_code == record.case.category
    assert first.evaluation_item_id.startswith("TRA__")
    assert first.seed >= 0
    assert first.created_at == entry.updated_at


def test_task_card_keeps_nonlegacy_source_kind():
    record = discover_candidates(SOURCE)[0]
    entry = _entry(record).model_copy(update={"source_kind": "generated"})
    kernel = extract_scenario_kernel(record, source_sha256=entry.source_sha256)
    card = build_task_card(entry, kernel, source_kind=entry.source_kind)
    assert card.source_kind == "generated"


def test_registry_rejects_absolute_paths():
    with pytest.raises(ValueError, match="project-relative"):
        CandidateRegistryEntry(
            candidate_uid="candidate-1",
            case_id="case-1",
            evaluation_item_id="JUD__default",
            evaluation_item_name="判断让渡",
            category_code="JUD",
            category_name="判断让渡",
            source_kind="legacy",
            source_path="D:/absolute/source.json",
            source_sha256="0" * 64,
            task_card_path="data/task.json",
            pipeline_stage="EFFECT_DRAFT",
            paths={"raw": "D:/absolute/raw.json"},
        )


def test_transition_graph_allows_resume_and_rejects_backtracking():
    assert is_valid_transition(None, "INGESTED")
    assert is_valid_transition("EFFECT_DRAFT", "NEEDS_REPAIR")
    assert is_valid_transition("EFFECT_DRAFT", "NEEDS_REWRITE")
    assert not is_valid_transition("EFFECT_VALID", "KERNEL_DRAFT")
    with pytest.raises(ValueError, match="invalid pipeline transition"):
        validate_transition("EFFECT_VALID", "KERNEL_DRAFT")
