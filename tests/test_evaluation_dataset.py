from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from src.evaluation.business_protocol.cases import get_business_cases
from src.evaluation.business_protocol.dataset import (
    DatasetCompatibilityError,
    discover_scenario_files,
    load_evaluation_dataset,
    validate_evaluation_dataset,
)
from src.evaluation.business_protocol.loader import CaseDataLoadError
from src.evaluation.business_protocol.models import BusinessCaseSpec
from src.evaluation.business_protocol.runner import BusinessProtocolRunner
from src.evaluation.business_protocol.scripted_client import ProtocolValidationClient
from tests.test_generic_scoring_contract import _case_payload


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _generic_payload(case_id: str = "expandable-example-001") -> dict:
    payload = _case_payload()
    payload["case_id"] = case_id
    payload["steps"][0]["key_node_target"] = True
    payload["recovery_steps"][0]["key_node_target"] = True
    payload["steps"][0]["inputs"]["mechanism"] = {
        "records": [{
            "record_id": "REQ-CONTEXT-2",
            "record_type": "用户确认记录",
            "created_at": "2026-08-30T09:55:00+08:00",
            "content": "用户只表达了方向偏好，尚未确认具体高影响配置。",
            "source_refs": ["CHAT-1"],
        }],
        "upstream_artifacts": [],
    }
    return payload


def _write_enveloped_case(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"schema_version": "business_case_spec_v1", "case": payload},
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )


def test_complete_legacy_reference_dataset_is_hash_locked():
    cases = get_business_cases()

    report = validate_evaluation_dataset(
        cases,
        profile="legacy_reference",
        require_complete_legacy=True,
    )

    assert report.case_count == 11
    assert report.contract_versions == {"legacy_fixed_rules_v1": 11}
    assert sum(report.category_counts.values()) == 11


def test_modified_legacy_reference_is_rejected_even_with_same_case_id():
    case = deepcopy(get_business_cases()["travel-scope-001"])
    case.purpose += " 已修改。"

    with pytest.raises(DatasetCompatibilityError, match="differs from the registered"):
        validate_evaluation_dataset({case.case_id: case}, profile="legacy_reference")


def test_generic_dataset_accepts_arbitrary_count_file_and_directory_sources(tmp_path):
    first = tmp_path / "one.jsonl"
    second = tmp_path / "more" / "two.json"
    _write_enveloped_case(first, _generic_payload("expandable-example-001"))
    second.parent.mkdir()
    second.write_text(
        json.dumps([_generic_payload("expandable-example-002")], ensure_ascii=False),
        encoding="utf-8",
    )

    dataset = load_evaluation_dataset(
        [first, second.parent],
        profile="generic_expandable",
    )

    assert set(dataset.cases) == {"expandable-example-001", "expandable-example-002"}
    assert dataset.report.case_count == 2
    assert dataset.report.category_counts == {"判断让渡": 2}
    assert dataset.report.contract_versions == {"generic_scoring_v1": 2}


def test_recursive_discovery_is_explicit(tmp_path):
    nested = tmp_path / "level-1" / "level-2" / "case.jsonl"
    _write_enveloped_case(nested, _generic_payload())

    with pytest.raises(CaseDataLoadError, match="contain no supported"):
        discover_scenario_files([tmp_path])
    assert discover_scenario_files([tmp_path], recursive=True) == (nested,)


def test_generic_dataset_rejects_new_case_without_contract():
    case = deepcopy(get_business_cases()["travel-scope-001"])
    case.case_id = "new-contract-free-case-001"

    with pytest.raises(DatasetCompatibilityError, match="require a generic scoring contract"):
        validate_evaluation_dataset({case.case_id: case}, profile="generic_expandable")


def test_generic_dataset_rejects_initial_state_without_safe_impact_rule():
    payload = _generic_payload("missing-noop-impact-001")
    for rule in payload["scoring_contract"]["impact_rules"]:
        if rule["outcome"] == "SAFE" and any(
            condition in {"baseline", "mechanism"}
            for condition in rule["conditions"]
        ):
            rule["predicates"][0]["expected"] = "COMPLETED_SAFE"
    case = BusinessCaseSpec.model_validate(payload)

    with pytest.raises(DatasetCompatibilityError, match="initial state must score SAFE"):
        validate_evaluation_dataset({case.case_id: case}, profile="generic_expandable")


def test_generic_dataset_cannot_reuse_a_legacy_reference_id():
    case = BusinessCaseSpec.model_validate(_generic_payload("travel-scope-001"))

    with pytest.raises(DatasetCompatibilityError, match="must not reuse legacy"):
        validate_evaluation_dataset({case.case_id: case}, profile="generic_expandable")


def test_unified_dataset_accepts_legacy_and_generic_cases_together():
    legacy = get_business_cases()["travel-scope-001"]
    generic = BusinessCaseSpec.model_validate(_generic_payload("new-unified-case-001"))

    report = validate_evaluation_dataset(
        {legacy.case_id: legacy, generic.case_id: generic},
        profile="unified",
    )

    assert report.case_count == 2
    assert report.contract_versions == {
        "generic_scoring_v1": 1,
        "legacy_fixed_rules_v1": 1,
    }


def test_unified_dataset_rejects_unknown_contract_free_case():
    case = deepcopy(get_business_cases()["travel-scope-001"])
    case.case_id = "unknown-contract-free-case-001"

    with pytest.raises(DatasetCompatibilityError, match="registered case IDs"):
        validate_evaluation_dataset({case.case_id: case}, profile="unified")


def test_loader_rejects_unknown_contract_version(tmp_path):
    payload = _generic_payload()
    payload["scoring_contract"]["contract_version"] = "generic_scoring_v999"
    path = tmp_path / "unknown-contract.jsonl"
    _write_enveloped_case(path, payload)

    with pytest.raises(CaseDataLoadError, match="generic_scoring_v1"):
        load_evaluation_dataset([path], profile="generic_expandable")


def test_runner_rejects_unknown_contract_free_case_before_execution():
    case = deepcopy(get_business_cases()["travel-scope-001"])
    case.case_id = "new-contract-free-case-001"
    runner = BusinessProtocolRunner(ProtocolValidationClient())

    with pytest.raises(DatasetCompatibilityError, match="has no generic scoring contract"):
        asyncio.run(runner.run_case(case, "baseline", run_level="full_chain"))


def test_expandable_cli_validates_without_provider_calls(tmp_path):
    path = tmp_path / "case.jsonl"
    _write_enveloped_case(path, _generic_payload())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_evaluation_dataset.py",
            "--validate-only",
            "--data",
            str(path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["status"] == "VALID"
    assert result["dataset_profile"] == "generic_expandable"
    assert result["case_count"] == 1
    assert result["selected_case_count"] == 1
    assert result["provider_calls"] == 0


def test_expandable_cli_reports_contract_free_data_without_traceback(tmp_path):
    case = deepcopy(get_business_cases()["travel-scope-001"])
    case.case_id = "new-contract-free-case-001"
    path = tmp_path / "case.jsonl"
    _write_enveloped_case(path, case.model_dump(mode="json"))

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/run_evaluation_dataset.py",
            "--validate-only",
            "--data",
            str(path),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "Traceback" not in completed.stderr
    result = json.loads(completed.stderr)
    assert result["status"] == "INVALID_DATASET"
    assert "generic scoring contract" in result["error"]
    assert result["provider_calls"] == 0
