from tests.test_scenario_authoring import _response_payload
from src.evaluation.scenario_generation.authoring import (
    AuthoringScoringOracle,
    compile_authoring_response,
)
from src.evaluation.scenario_generation.path_validation import (
    SixPathValidationReport,
    validate_six_paths,
)
from src.evaluation.scenario_generation.pipeline import (
    authoring_to_runtime_condition,
    runtime_to_authoring_condition,
)


def _compiled_case_and_oracle():
    payload = _response_payload()
    case = compile_authoring_response(
        payload,
        case_id="jud-path-validation-001",
        category="判断让渡",
    )
    oracle = AuthoringScoringOracle.model_validate(payload["scoring_oracle"])
    return case, oracle


def test_six_path_report_covers_matrix_and_records_state_evidence():
    case, oracle = _compiled_case_and_oracle()
    report = validate_six_paths(case, oracle)

    assert report.status == "PASS"
    assert report.all_paths_passed is True
    assert {item.path_id for item in report.semantic_paths} == {
        "baseline_safe",
        "baseline_unsafe",
        "mechanism_safe",
        "mechanism_unsafe",
        "recovery_success",
        "recovery_failure",
    }
    assert len(report.execution_matrix) == 6
    unsafe = next(item for item in report.semantic_paths if item.path_id == "mechanism_unsafe")
    assert any(
        call.tool_name == "apply_unconfirmed_plan"
        for attempt in unsafe.attempts
        for observation in attempt.step_observations
        for call in observation.tool_calls
    )
    assert any(
        change.path == "decision_status"
        for attempt in unsafe.attempts
        for change in attempt.state_changes
    )
    assert SixPathValidationReport.model_validate_json(report.model_dump_json()) == report


def test_six_path_report_rejects_recovery_that_leaves_risk_state():
    case, oracle = _compiled_case_and_oracle()
    # Mutate only the compiled effect after the compiler gate.  The validator
    # must catch the semantic regression independently of the compiler.
    recovery_tool = case.recovery_steps[0].tools[0]
    recovery_tool.state_updates["recovery"]["decision_status"] = "EXECUTED_UNCONFIRMED"

    report = validate_six_paths(case, oracle)

    assert report.status == "FAIL"
    recovery = next(item for item in report.semantic_paths if item.path_id == "recovery_success")
    assert recovery.status == "FAIL"
    assert any("final impact" in error for error in recovery.errors)


def test_condition_mapping_is_explicit_and_rejects_unknown_labels():
    assert runtime_to_authoring_condition("baseline") == "normal"
    assert runtime_to_authoring_condition("mechanism") == "risk"
    assert runtime_to_authoring_condition("recovery") == "recovery"
    assert authoring_to_runtime_condition("normal") == "baseline"
    assert authoring_to_runtime_condition("risk") == "mechanism"
    assert authoring_to_runtime_condition("recovery") == "recovery"

    import pytest

    with pytest.raises(ValueError, match="unknown runtime condition"):
        runtime_to_authoring_condition("basline")
    with pytest.raises(ValueError, match="unknown authoring condition"):
        authoring_to_runtime_condition("danger")
