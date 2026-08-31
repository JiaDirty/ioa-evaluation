import json
import pytest

from scripts.merge_migrated_datasets import merge
from src.evaluation.business_protocol.loader import CaseDataLoadError
from tests.test_evaluation_dataset import _generic_payload, _write_enveloped_case


def test_merge_migrated_datasets_validates_and_writes_one_generic_file(tmp_path):
    first = tmp_path / "migration-a"
    second = tmp_path / "migration-b"
    first.mkdir()
    second.mkdir()
    for directory, case_id in ((first, "merged-a"), (second, "merged-b")):
        payload = _generic_payload(case_id)
        _write_enveloped_case(directory / "migrated_cases.jsonl", payload)
        (directory / "migration_summary.json").write_text(
            json.dumps({
                "candidate_count": 1,
                "migrated_count": 1,
                "quarantined_count": 0,
                "review_status": "CONTRACT_DRAFTS_REQUIRE_SEMANTIC_AND_HUMAN_REVIEW",
            }),
            encoding="utf-8",
        )

    output = tmp_path / "merged"
    summary = merge([first, second], output)

    assert summary["case_count"] == 2
    assert summary["dataset_profile"] == "generic_expandable"
    lines = (output / "accepted_cases.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(json.loads(line)["case"]["scoring_contract"] for line in lines)


def test_merge_migrated_datasets_rejects_duplicate_case_ids(tmp_path):
    first = tmp_path / "migration-a"
    second = tmp_path / "migration-b"
    first.mkdir()
    second.mkdir()
    for directory in (first, second):
        _write_enveloped_case(
            directory / "migrated_cases.jsonl",
            _generic_payload("same-case-id"),
        )

    with pytest.raises(CaseDataLoadError, match="duplicate case_id"):
        merge([first, second], tmp_path / "merged")
