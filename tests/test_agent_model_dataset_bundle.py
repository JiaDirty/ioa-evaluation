from src.evaluation.agent_model.dataset_bundle import (
    GENERATED_DATASET,
    assert_generated_dataset_current,
    build_dataset_bytes,
)


def test_generated_dataset_matches_canonical_case_files():
    assert build_dataset_bytes() == GENERATED_DATASET.read_bytes()
    assert_generated_dataset_current()
