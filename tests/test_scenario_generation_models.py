from copy import deepcopy

import pytest
from pydantic import ValidationError

from src.evaluation.business_protocol.cases import get_business_cases
from src.evaluation.scenario_generation import ScenarioGenerationBatch
from src.llm.client import _response_format_payload


def _batch_payload() -> dict:
    cases = [
        case.model_dump(mode="json")
        for case in list(get_business_cases().values())[:3]
    ]
    return {
        "prompt_version": "ioa_scenario_generation_v4",
        "generation_status": "COMPLETED",
        "generation_config": {
            "target_category": "CAS",
            "scenario_count": 3,
            "batch_id": "CAS-B01",
            "generator_id": "aihubmix",
            "generator_model_id": "generator-model",
            "generation_seed": 2026082701,
            "excluded_case_ids": [],
            "excluded_scenario_count": 0,
        },
        "cases": cases,
        "batch_audit": {
            "case_count_matches_request": True,
            "unique_case_ids": True,
            "unique_industry_domains": True,
            "unique_business_actions": True,
            "unique_chain_or_round_structures": True,
            "excluded_scenarios_not_reused": True,
            "all_cases_pass_hard_gates": True,
            "known_open_questions": [],
        },
    }


def test_completed_batch_accepts_exact_matching_cases():
    payload = _batch_payload()
    for case in payload["cases"]:
        case["category"] = "CAS"

    batch = ScenarioGenerationBatch.model_validate(payload)

    assert len(batch.cases) == 3


def test_completed_batch_rejects_wrong_category():
    payload = _batch_payload()

    with pytest.raises(ValidationError, match="outside target category"):
        ScenarioGenerationBatch.model_validate(payload)


def test_failed_batch_requires_empty_cases_and_reason():
    payload = deepcopy(_batch_payload())
    payload["generation_status"] = "FAILED_QUALITY_GATE"
    payload["cases"] = []
    payload["batch_audit"]["case_count_matches_request"] = False
    payload["batch_audit"]["all_cases_pass_hard_gates"] = False
    payload["batch_audit"]["known_open_questions"] = ["因果配对未通过"]

    batch = ScenarioGenerationBatch.model_validate(payload)

    assert batch.cases == []


def test_aihubmix_client_builds_strict_json_schema_for_batch():
    response_format = _response_format_payload(ScenarioGenerationBatch)

    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    assert response_format["json_schema"]["name"] == "ScenarioGenerationBatch"
