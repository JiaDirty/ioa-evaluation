import json
from pathlib import Path

from run_experiment import _evaluate_scenario_execution
from src.attacks.base import AttackContext
from src.attacks.registry import DEFAULT_ATTACK_ADAPTER_REGISTRY
from src.core.data_models import TaskResult, TaskStatus
from src.evaluation.attack_evaluation_bundle import AttackEvaluationBundle
from src.experiment.scenario_loader import ScenarioLoader
from src.judging import AttackJudgeAgent
from src.judging.rubric_registry import DEFAULT_RUBRIC_REGISTRY


def test_all_18_adapters_and_rubrics_are_registered():
    adapters = DEFAULT_ATTACK_ADAPTER_REGISTRY.list_attack_types()
    rubrics = DEFAULT_RUBRIC_REGISTRY.list_attack_types()
    assert len(adapters) == 18
    assert adapters == rubrics
    for attack_type in adapters:
        adapter = DEFAULT_ATTACK_ADAPTER_REGISTRY.create(attack_type)
        rubric = DEFAULT_RUBRIC_REGISTRY.get(attack_type)
        assert adapter.success_stages
        assert rubric.core_stage in rubric.stages


def test_seed_files_have_no_preset_attack_outcome_fields():
    banned = [
        "attack_" + "should_" + "succeed",
        "expected_" + "attack_" + "success",
        "expected_" + "blocked",
        "expected_" + "verdict",
        "expected_" + "outcome",
    ]
    for path in sorted(Path("data/seeds").glob("seed_*.json")):
        text = path.read_text(encoding="utf-8")
        for token in banned:
            assert token not in text, path.name
        scenario = ScenarioLoader(path).load()
        assert scenario.attack.adapter == scenario.attack.attack_type
        assert scenario.attack.success_stages
        assert scenario.attack.required_evidence


def test_judge_cites_only_real_evidence_ids():
    context = AttackContext(
        attack_id="a",
        scenario_id="s",
        attack_type="delegation_drift",
        objective="mutate scope",
        target_component="gateway",
        target_sub_ioa="travel",
        success_stages=["scope_mutated", "expanded_scope_accepted"],
        required_evidence=["requested_scopes", "delegation"],
    )
    context.prepared = True
    context.record(
        stage="scope_mutated",
        injection_applied=True,
        modified_object="delegation_request",
        after_state={"requested_scopes": ["read", "write"]},
        details={"delegation": "probe"},
    )
    bundle = AttackEvaluationBundle(
        experiment={"scenario_id": "s", "task_id": "t", "execution_mode": "offline_deterministic"},
        attack_specification={
            "attack_type": "delegation_drift",
            "objective": "mutate scope",
            "success_stages": context.success_stages,
            "required_evidence": context.required_evidence,
        },
        attack_injection={
            "prepared": context.prepared,
            "triggered": context.triggered,
            "injection_applied": context.injection_applied,
            "logs": [log.model_dump(mode="json") for log in context.attack_logs],
        },
        evidence_index={
            log.evidence_id: {"source": "attack_log"}
            for log in context.attack_logs
        },
    )
    verdict = AttackJudgeAgent().judge(bundle)
    known = bundle.all_evidence_ids()
    assert verdict.evidence
    assert all(citation.event_id in known for citation in verdict.evidence)
    assert "delegation_controller" in verdict.vulnerability.components


def test_missing_adapter_or_judge_makes_scenario_invalid():
    scenario = ScenarioLoader("data/seeds/seed_001_identity_spoofing.json").load()
    evaluation = _evaluate_scenario_execution(
        scenario,
        TaskResult(task_id="b", status=TaskStatus.COMPLETED),
        TaskResult(task_id="a", status=TaskStatus.COMPLETED),
        None,
        None,
        None,
        None,
    )
    assert not evaluation["scenario_valid"]
    assert not evaluation["passed"]


def test_successful_attack_is_not_legacy_pass():
    scenario = ScenarioLoader("data/seeds/seed_015_incentive_mismatch.json").load()
    context = AttackContext(
        attack_id="a",
        scenario_id=scenario.scenario_id,
        attack_type="incentive_mismatch",
        objective="mutate incentives",
        target_component="marketplace",
        target_sub_ioa="news",
    )
    context.prepared = True
    context.injection_applied = True

    class _Outcome:
        status = type("Status", (), {"value": "SUCCESS"})()
        maximum_stage = "private_reward_increased"
        attack_succeeded = True
        consequence_realized = False

    class _Verdict:
        outcome = _Outcome()
        missing_evidence = []
        trigger_assessment = type("Trigger", (), {"triggered": True})()
        system_response = type("Response", (), {"blocked": False, "recovered": False})()
        vulnerability = type("Vulnerability", (), {"components": ["marketplace"]})()
        evidence = []
        reasoning_summary = "success"

    evaluation = _evaluate_scenario_execution(
        scenario,
        TaskResult(task_id="b", status=TaskStatus.COMPLETED),
        TaskResult(task_id="a", status=TaskStatus.COMPLETED),
        None,
        AttackEvaluationBundle(),
        _Verdict(),
        context,
    )
    assert evaluation["scenario_valid"]
    assert evaluation["attack_succeeded"]
    assert not evaluation["passed"]
