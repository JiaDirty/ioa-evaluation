from __future__ import annotations

from types import SimpleNamespace

import pytest

from scripts.review_candidate_rotation import (
    EXPECTED_ITEMS,
    GENERATOR_TO_REVIEWER,
    MODEL_RING,
    REVIEWER_TO_GENERATOR,
    select_rotation_records,
    validate_candidate_matrix,
)


def _records():
    records = []
    for item_name in EXPECTED_ITEMS:
        for model_id in MODEL_RING:
            for ordinal in range(1, 6):
                uid = f"{item_name}::{model_id}::case-{ordinal}"
                records.append(SimpleNamespace(
                    item_name=item_name,
                    generator_model_id=model_id,
                    candidate_uid=uid,
                ))
    return records


def test_rotation_is_closed_and_has_no_self_review():
    assert len(REVIEWER_TO_GENERATOR) == 8
    assert set(GENERATOR_TO_REVIEWER) == set(MODEL_RING)
    assert set(GENERATOR_TO_REVIEWER.values()) == set(MODEL_RING)
    assert all(generator != reviewer for generator, reviewer in GENERATOR_TO_REVIEWER.items())
    assert GENERATOR_TO_REVIEWER["deepseek-v4-flash"] == "gpt-5.6-luna"
    assert GENERATOR_TO_REVIEWER["gpt-5.6-luna"] == "qwen3.8-flash"


def test_stable_selection_has_expected_coverage():
    records = _records()
    first = select_rotation_records(records)
    second = select_rotation_records(list(reversed(records)))
    assert [(row.candidate_uid, reviewer) for row, reviewer in first] == [
        (row.candidate_uid, reviewer) for row, reviewer in second
    ]
    assert len(first) == 176
    for item_name in EXPECTED_ITEMS:
        item_rows = [(row, reviewer) for row, reviewer in first if row.item_name == item_name]
        assert len(item_rows) == 16
        for model_id in MODEL_RING:
            model_rows = [row for row, _ in item_rows if row.generator_model_id == model_id]
            assert len(model_rows) == 2
    assert all(row.generator_model_id != reviewer for row, reviewer in first)


def test_candidate_matrix_rejects_missing_candidate():
    records = _records()
    with pytest.raises(ValueError, match="候选总数应为 440"):
        validate_candidate_matrix(records[:-1])


def test_candidate_matrix_rejects_duplicate_uid():
    records = _records()
    records[-1].candidate_uid = records[0].candidate_uid
    with pytest.raises(ValueError, match="candidate_uid 不唯一"):
        validate_candidate_matrix(records)


def test_candidate_matrix_rejects_wrong_model_with_same_total():
    records = _records()
    records[-1].generator_model_id = "unexpected-model"
    with pytest.raises(ValueError, match="生成模型集合不匹配"):
        validate_candidate_matrix(records)


def test_candidate_matrix_rejects_unbalanced_group_with_same_total():
    records = _records()
    records[-1].item_name = EXPECTED_ITEMS[0]
    with pytest.raises(ValueError, match="恰有 5 条候选"):
        validate_candidate_matrix(records)
