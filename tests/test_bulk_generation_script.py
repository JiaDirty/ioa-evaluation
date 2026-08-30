import pytest

from scripts.run_bulk_candidate_generation import configured_models


def test_configured_models_can_select_one_enabled_model():
    models = configured_models(["gpt-5.6-sol"])
    assert [name for name, _profile in models] == ["gpt-5.6-sol"]


def test_configured_models_rejects_unknown_or_disabled_model():
    with pytest.raises(ValueError, match="not enabled or configured"):
        configured_models(["claude-haiku-4-5"])
