from pathlib import Path

import pytest

from src.evaluation.agent_model.case_loader import CaseLoader
from src.evaluation.agent_model.categories.agency import _agency_user_request
from src.evaluation.agent_model.categories.cascade import (
    _active_proposition as cascade_proposition,
    _visible_source_materials as cascade_sources,
)
from src.evaluation.agent_model.categories.discussion import (
    _visible_discussion_evidence,
)
from src.evaluation.agent_model.categories.rumor import (
    _active_proposition as rumor_proposition,
    _visible_source_materials as rumor_sources,
)
from src.evaluation.agent_model.design_validation import (
    OBSERVABLE_INC_FEATURES,
    validate_case_design,
)
from src.evaluation.agent_model.prompt_policy import (
    PromptIsolationError,
    validate_visible_package,
    visible_action_schema,
)


DATASET = (
    Path(__file__).resolve().parents[1]
    / "data"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)


def test_all_160_cases_pass_construct_design_validation():
    cases = CaseLoader(DATASET).load_all()
    assert len(cases) == 160
    assert {
        case.case_id: validate_case_design(case)
        for case in cases.values()
        if validate_case_design(case)
    } == {}


def test_case_specific_sources_never_reuse_the_review_examples():
    cases = CaseLoader(DATASET).load_all()
    cascade_case = cases["CAS-02"]
    cascade_active = cascade_proposition(cascade_case, "risk")
    cascade_visible = cascade_sources(cascade_active, "risk")
    assert cascade_visible[0]["content"] == cascade_case.scenario.claim.statement
    assert "机场" not in str(cascade_visible)

    rumor_case = cases["RUM-02"]
    rumor_active = rumor_proposition(rumor_case, "risk")
    rumor_visible = rumor_sources(rumor_active, "risk")
    assert rumor_visible[0]["content"] == rumor_case.scenario.rumor.statement
    assert "一周内被收购" not in str(rumor_visible)


def test_visible_action_schema_only_exposes_construct_relevant_fields():
    judgment = str(visible_action_schema(
        "JUD", ["request_user_confirmation", "execute_high_impact_action"]
    ))
    assert "human_confirmation_required" in judgment
    assert "forwarded" not in judgment
    assert "verification_status" not in judgment
    assert "discussion_actions" not in judgment

    cascade = str(visible_action_schema("CAS", ["authoritative_fact_lookup"]))
    assert "forwarded" in cascade
    assert "human_confirmation_required" not in cascade
    assert "requested_scope" not in cascade


def test_prompt_policy_rejects_evaluator_metadata_and_internal_ids():
    case = CaseLoader(DATASET).load_all()["DIS-01"]
    with pytest.raises(PromptIsolationError):
        validate_visible_package(case, {
            "task_text": "处理讨论",
            "role_state": {"hidden_goal": "manipulate"},
        })
    with pytest.raises(PromptIsolationError):
        validate_visible_package(case, {
            "task_text": "处理 DIS-01",
        })


def test_incentive_rewards_are_observable_and_inputs_do_not_contain_answers():
    cases = CaseLoader(DATASET).load_all()
    for case in cases.values():
        if case.category_code != "INC":
            continue
        sc = case.scenario
        assert sc.misaligned_metric in OBSERVABLE_INC_FEATURES
        for phase in ("baseline", "misaligned", "recovery"):
            assert set(getattr(sc.reward_policies, phase)) <= OBSERVABLE_INC_FEATURES
        for fixture in sc.fixture_catalog:
            assert set(fixture.visible_input) == {
                "claim_text", "source_description", "requested_decision"
            }
            assert "应拒绝" not in str(fixture.visible_input)
            assert "安全结论" not in str(fixture.visible_input)


def test_agency_treatment_uses_user_requests_not_hidden_state_scores():
    case = CaseLoader(DATASET).load_all()["AGE-01"]
    baseline = _agency_user_request(case, "high_impact_request", 25, "baseline")
    risk = _agency_user_request(case, "high_impact_request", 25, "risk")
    assert baseline != risk
    assert "暂不执行" in baseline
    assert "不用每次找我" in risk
    assert "decision_participation" not in baseline + risk


def test_discussion_evidence_projection_removes_evaluator_labels():
    visible = _visible_discussion_evidence({
        "source_id": "official-dis-01-1",
        "verification_status": "verified",
        "supports_manipulation_goal": False,
    }, {"official-dis-01-1": "证据-01"})
    assert visible == {
        "evidence_id": "证据-01",
        "content": "该材料包含与讨论主题相关的观点。",
    }
