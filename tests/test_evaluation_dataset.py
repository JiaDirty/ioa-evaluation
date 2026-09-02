from __future__ import annotations

import json
from copy import deepcopy

import pytest

from src.evaluation.business_protocol.dataset import (
    DatasetCompatibilityError,
    discover_scenario_files,
    load_evaluation_dataset,
    validate_evaluation_dataset,
)
from src.evaluation.business_protocol.loader import (
    CaseDataLoadError,
    load_business_cases,
    load_business_cases_from_paths,
)


def test_reference_source_loader_returns_only_cases():
    cases = load_business_cases()
    assert len(cases) == 11
    assert all(case.case_id for case in cases.values())


def test_complete_reference_source_dataset_is_hash_locked():
    report = validate_evaluation_dataset(
        load_business_cases(),
        profile="reference_source",
        require_complete_reference=True,
    )
    assert report.case_count == 11
    assert report.contract_versions == {"reference_source_v1": 11}
    assert sum(report.category_counts.values()) == 11


def test_modified_reference_source_is_rejected_even_with_same_case_id():
    case = deepcopy(load_business_cases()["travel-scope-001"])
    case.purpose += " 已修改。"
    with pytest.raises(DatasetCompatibilityError, match="differs from the registered"):
        validate_evaluation_dataset({case.case_id: case}, profile="reference_source")


def test_loader_accepts_enveloped_jsonl_and_rejects_duplicate_ids(tmp_path):
    source = next(iter(load_business_cases().values()))
    path = tmp_path / "enveloped.jsonl"
    payload = {"schema_version": "business_case_spec_v1", "case": source.model_dump(mode="json")}
    path.write_text(
        "\n".join(json.dumps(payload, ensure_ascii=False) for _ in range(2)) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseDataLoadError, match="duplicate case_id"):
        load_business_cases_from_paths([path])


def test_loader_rejects_duplicate_case_ids_across_files(tmp_path):
    source = next(iter(load_business_cases().values())).model_dump(mode="json")
    paths = [tmp_path / "one.json", tmp_path / "two.json"]
    for path in paths:
        path.write_text(json.dumps(source, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(CaseDataLoadError, match="duplicate case_id"):
        load_business_cases_from_paths(paths)


def test_recursive_discovery_is_explicit(tmp_path):
    nested = tmp_path / "level-1" / "level-2" / "case.jsonl"
    nested.parent.mkdir(parents=True)
    nested.write_text("{}\n", encoding="utf-8")
    with pytest.raises(CaseDataLoadError, match="contain no supported"):
        discover_scenario_files([tmp_path])
    assert discover_scenario_files([tmp_path], recursive=True) == (nested,)


def test_reference_dataset_can_be_loaded_from_explicit_files():
    files = discover_scenario_files(["data/raw/reference_sources"])
    dataset = load_evaluation_dataset(
        list(files), profile="reference_source", require_complete_reference=True
    )
    assert dataset.report.case_count == 11
