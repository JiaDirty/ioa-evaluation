from pathlib import Path

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.scheduler import (
    assert_provider_version_stable,
    planned_case_order,
)


DATASET = Path(__file__).resolve().parents[1] / "data" / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"


def test_seeded_order_is_deterministic_and_category_interleaved():
    cases = list(CaseLoader(DATASET).load_all().values())
    first = planned_case_order(cases, 7)
    second = planned_case_order(cases, 7)
    assert [case.case_id for case in first] == [case.case_id for case in second]
    assert len({case.category_code for case in first[:8]}) == 8


def test_provider_version_drift_fails_closed():
    assert_provider_version_stable(["model-v1", "model-v1"])
    try:
        assert_provider_version_stable(["model-v1", "model-v2"])
    except RuntimeError as exc:
        assert "drift" in str(exc)
    else:
        raise AssertionError("model version drift was not rejected")
