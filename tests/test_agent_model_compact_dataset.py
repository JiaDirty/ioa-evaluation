import json

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.dataset_bundle import (
    CASE_DIR,
    CATEGORY_FILES,
    GENERATED_DATASET,
)


def test_compact_authoring_rows_expand_to_generated_dataset():
    generated = CaseLoader(GENERATED_DATASET).expanded_dicts()
    authored = []
    for filename in CATEGORY_FILES:
        authored.extend(CaseLoader(CASE_DIR / filename).expanded_dicts())

    assert authored == generated


def test_compact_rows_omit_inherited_policy_blocks():
    inherited_fields = {
        "schema_version",
        "model_config",
        "data_policy",
        "visibility_policy",
        "context_policy",
        "result_layers",
    }
    for filename in CATEGORY_FILES:
        first = json.loads((CASE_DIR / filename).read_text(encoding="utf-8").splitlines()[0])
        assert inherited_fields.isdisjoint(first)


def test_compact_authoring_files_are_smaller_than_expanded_exchange_file():
    compact_bytes = sum((CASE_DIR / filename).stat().st_size for filename in CATEGORY_FILES)

    assert compact_bytes < GENERATED_DATASET.stat().st_size * 0.85
