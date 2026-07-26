import json
from pathlib import Path

from src.evaluation.agent_model.case_loader import CaseLoader


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


def test_v2_dataset_passes_strict_validation():
    loader = CaseLoader(DATASET)

    report = loader.validate_only()

    assert report["valid_cases"] == 160
    assert report["errors"] == 0
    assert report["by_category"] == {
        "CAS": 20,
        "CON": 20,
        "RUM": 20,
        "NOR": 20,
        "INC": 20,
        "JUD": 20,
        "DIS": 20,
        "AGE": 20,
    }


def test_loader_rejects_missing_category_field(tmp_path):
    case = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    del case["scenario"]["ground_truth"]
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    loader = CaseLoader(path)
    report = loader.validate_only(expected_total=None, expected_per_category=None)

    assert report["valid_cases"] == 0
    assert report["errors"] == 1
    assert "ground_truth" in report["error_details"][0]["message"]


def test_loader_rejects_unknown_common_field(tmp_path):
    case = json.loads(DATASET.read_text(encoding="utf-8").splitlines()[0])
    case["unexpected"] = "must be rejected"
    path = tmp_path / "invalid.jsonl"
    path.write_text(json.dumps(case, ensure_ascii=False) + "\n", encoding="utf-8")

    loader = CaseLoader(path)
    report = loader.validate_only(expected_total=None, expected_per_category=None)

    assert report["valid_cases"] == 0
    assert report["errors"] == 1
    assert "unexpected" in report["error_details"][0]["message"]


def test_loader_reports_incomplete_dataset(tmp_path):
    first = DATASET.read_text(encoding="utf-8").splitlines()[0]
    path = tmp_path / "one.jsonl"
    path.write_text(first + "\n", encoding="utf-8")

    report = CaseLoader(path).validate_only()

    assert report["valid_cases"] == 1
    assert report["errors"] > 0
    assert any("Expected 160" in item["message"] for item in report["error_details"])


def test_incentive_cases_have_distinct_balanced_round_materials():
    cases = CaseLoader(DATASET).load_all()

    for case in (item for item in cases.values() if item.category_code == "INC"):
        fixtures = case.scenario.fixture_catalog
        fixture_by_key = {item.fixture_key: item for item in fixtures}
        claims = [item.visible_input["claim_text"] for item in fixtures]
        assert len(claims) == 30
        assert len(set(claims)) == 30
        for phase in ("baseline", "misaligned", "recovery"):
            support = {
                fixture_by_key[task.fixture_key].authoritative_tool_response["supported"]
                for task in case.scenario.task_sequence
                if task.phase == phase
            }
            assert support == {False, True}
