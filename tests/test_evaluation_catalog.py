from src.evaluation.business_protocol.cases import get_business_cases
from src.evaluation.business_protocol.models import AgentBusinessResult
from src.evaluation.catalog import load_evaluation_catalog


def test_catalog_is_the_ten_item_source_of_truth():
    catalog = load_evaluation_catalog()

    assert catalog.category_codes == (
        "CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE", "TRA", "CRE",
    )
    assert len(catalog.categories) == 10
    assert set(catalog.protocol_case_ids) == set(get_business_cases())


def test_catalog_result_fields_match_runtime_schema():
    catalog = load_evaluation_catalog()

    assert catalog.canonical_result_fields == list(AgentBusinessResult.model_fields)


def test_catalog_marks_legacy_dataset_as_eight_of_ten():
    catalog = load_evaluation_catalog()
    counts = {item.code: item.legacy_dataset_cases for item in catalog.categories}

    assert all(counts[code] == 20 for code in catalog.category_codes[:8])
    assert counts["TRA"] == 0
    assert counts["CRE"] == 0
