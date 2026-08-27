import json

import pytest

from src.evaluation.business_protocol.cases import (
    get_builtin_business_cases,
    get_business_cases,
)
from src.evaluation.business_protocol.loader import (
    CaseDataLoadError,
    load_business_cases_from_paths,
)


def test_external_cases_are_the_canonical_equivalent_of_builtin_cases():
    builtin = get_builtin_business_cases()
    external = get_business_cases()

    assert set(external) == set(builtin)
    assert {
        case_id: case.model_dump(mode="json")
        for case_id, case in external.items()
    } == {
        case_id: case.model_dump(mode="json")
        for case_id, case in builtin.items()
    }


def test_loader_accepts_direct_json_array_and_enveloped_jsonl(tmp_path):
    builtin = get_builtin_business_cases()
    direct_source = builtin["travel-scope-001"]
    enveloped_source = builtin["batch-consensus-001"]
    direct_path = tmp_path / "direct.json"
    direct_path.write_text(
        json.dumps([direct_source.model_dump(mode="json")], ensure_ascii=False),
        encoding="utf-8",
    )
    enveloped_path = tmp_path / "enveloped.jsonl"
    enveloped_path.write_text(
        json.dumps(
            {
                "schema_version": "business_case_spec_v1",
                "case": enveloped_source.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    cases = load_business_cases_from_paths([direct_path, enveloped_path])

    assert cases["travel-scope-001"].title == direct_source.title
    assert cases["batch-consensus-001"].title == enveloped_source.title


def test_loader_rejects_duplicate_case_ids(tmp_path):
    source = get_builtin_business_cases()["travel-scope-001"].model_dump(mode="json")
    path = tmp_path / "duplicate.jsonl"
    path.write_text(
        "\n".join(json.dumps(source, ensure_ascii=False) for _ in range(2)) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(CaseDataLoadError, match="duplicate case_id"):
        load_business_cases_from_paths([path])
