from src.evaluation.business_protocol.loader import load_business_cases
from src.evaluation.business_protocol.models import AgentBusinessResult
from src.evaluation.scenario_generation.catalog import load_evaluation_catalog


def test_catalog_is_the_single_source_of_truth():
    catalog = load_evaluation_catalog()

    assert catalog.category_codes == (
        "CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE", "TRA", "CRE",
    )
    assert len(catalog.categories) == 10
    assert len(catalog.branches) == 11
    assert set(catalog.protocol_case_ids) == set(load_business_cases())
    assert catalog.category_names_zh[0] == "跨系统级联扩散"
    assert catalog.code_for_name_zh("资格申报") == catalog.category_codes[-1]


def test_catalog_result_fields_match_runtime_schema():
    catalog = load_evaluation_catalog()

    assert catalog.formal_result_fields == list(AgentBusinessResult.model_fields)


def test_catalog_does_not_expose_archived_dataset_fields():
    catalog = load_evaluation_catalog()

    assert all(
        "legacy_dataset_cases" not in item.model_dump()
        for item in catalog.categories
    )
