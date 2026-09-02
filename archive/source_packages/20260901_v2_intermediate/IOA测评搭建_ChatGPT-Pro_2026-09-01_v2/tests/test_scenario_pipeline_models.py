from pathlib import Path

import pytest
from pydantic import ValidationError

from src.evaluation.candidate_review import discover_candidates
from src.evaluation.scenario_generation.pipeline import (
    extract_effect_spec,
    extract_scenario_kernel,
    sha256_file,
)
from src.evaluation.scenario_generation.pipeline_models import (
    EffectSpec,
    ScenarioKernel,
    upgrade_effect_spec_payload,
    upgrade_kernel_payload,
    verify_effect_kernel_binding,
    verify_effect_spec_hash,
    verify_kernel_hash,
)


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data" / "candidate_batches" / "批量生成-第01轮"


@pytest.fixture(scope="module")
def legacy_record():
    return discover_candidates(SOURCE)[0]


def test_legacy_kernel_is_sealed_and_tamper_is_detected(legacy_record):
    kernel = extract_scenario_kernel(
        legacy_record,
        source_sha256=sha256_file(legacy_record.source_path),
    )
    assert verify_kernel_hash(kernel) == kernel.content_sha256
    tampered = kernel.model_copy(update={"title": kernel.title + "（改动）"})
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_kernel_hash(tampered)


def test_repeated_extraction_keeps_semantic_hash_stable(legacy_record):
    first = extract_scenario_kernel(
        legacy_record,
        source_sha256=sha256_file(legacy_record.source_path),
    )
    second = extract_scenario_kernel(
        legacy_record,
        source_sha256=sha256_file(legacy_record.source_path),
    )
    assert first.content_sha256 == second.content_sha256
    first_effect = extract_effect_spec(legacy_record, first)
    second_effect = extract_effect_spec(legacy_record, second)
    assert first_effect.content_sha256 == second_effect.content_sha256


def test_legacy_effect_is_explicit_draft_and_hash_is_sealed(legacy_record):
    kernel = extract_scenario_kernel(legacy_record)
    effect = extract_effect_spec(legacy_record, kernel)
    assert effect.status == "DRAFT"
    assert all(step.behavior is None for step in effect.steps)
    assert verify_effect_spec_hash(effect) == effect.content_sha256


def test_effect_kernel_binding_rejects_a_different_kernel(legacy_record):
    kernel = extract_scenario_kernel(legacy_record)
    effect = extract_effect_spec(legacy_record, kernel)
    other = kernel.model_copy(update={"kernel_id": "kernel-other-1234"})
    # The modified kernel is intentionally unsealed; either the hash check or
    # the ID check must reject it before compilation.
    with pytest.raises(ValueError):
        verify_effect_kernel_binding(other, effect)


def test_unknown_intermediate_versions_are_rejected():
    with pytest.raises(ValueError, match="unsupported ScenarioKernel"):
        upgrade_kernel_payload({"schema_version": "scenario_kernel_v99"})
    with pytest.raises(ValueError, match="unsupported EffectSpec"):
        upgrade_effect_spec_payload({"schema_version": "effect_spec_v99"})


def test_effect_payload_with_unknown_field_is_rejected():
    payload = {
        "schema_version": "effect_spec_v1",
        "effect_id": "effect-test-1234",
        "kernel_id": "kernel-test-1234",
        "kernel_sha256": "0" * 64,
        "status": "DRAFT",
        "steps": [],
        "source": {"source_kind": "legacy_extracted"},
        "unexpected": True,
    }
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EffectSpec.model_validate(payload)
