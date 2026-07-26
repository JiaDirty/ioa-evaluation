import pytest

from src.evaluation.agent_model.context_projection import (
    ContextOverflowError,
    ContextProjectionPolicy,
    TaintedContextError,
    project_context,
)


def test_projection_rejects_nested_evaluation_taint():
    with pytest.raises(TaintedContextError):
        project_context(
            {"artifact": {"renamed_hidden_field": {"taint": "evaluation_only", "value": "canary"}}},
            ContextProjectionPolicy(max_tokens=100),
        )


def test_projection_fails_closed_instead_of_truncating_required_context():
    with pytest.raises(ContextOverflowError) as exc:
        project_context(
            {"formal_rule": "x" * 1000},
            ContextProjectionPolicy(max_tokens=10, required_context_paths=("formal_rule",)),
        )
    assert exc.value.code == "INVALID_CONTEXT_OVERFLOW"


def test_projection_records_token_contract_without_removals():
    result = project_context(
        {"formal_rule": "keep"},
        ContextProjectionPolicy(max_tokens=100, required_context_paths=("formal_rule",)),
    )
    assert result.required_complete
    assert result.removed_paths == ()
    assert result.projected_tokens == result.estimated_input_tokens
