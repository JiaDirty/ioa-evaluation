import pytest

from scripts.generate_candidate_batch import response_handler_for_version
from scripts.run_bulk_candidate_generation import configured_models


def test_configured_models_can_select_one_enabled_model():
    models = configured_models(["gpt-5.6-sol"])
    assert [name for name, _profile in models] == ["gpt-5.6-sol"]


def test_configured_models_rejects_unknown_or_disabled_model():
    with pytest.raises(ValueError, match="not enabled or configured"):
        configured_models(["claude-haiku-4-5"])


def test_generation_entry_supports_authoring_and_legacy_blueprint_versions():
    authoring_model, authoring_compiler = response_handler_for_version(
        "ioa_scenario_generation_v7_authoring"
    )
    blueprint_model, blueprint_compiler = response_handler_for_version(
        "ioa_scenario_generation_v9_blueprint_sequences"
    )

    assert authoring_model.__name__ == "AuthoringScenarioResponse"
    assert authoring_compiler.__name__ == "compile_authoring_response"
    assert blueprint_model.__name__ == "BlueprintScenarioResponse"
    assert blueprint_compiler.__name__ == "compile_blueprint_response"


def test_generation_entry_rejects_unknown_prompt_version():
    with pytest.raises(ValueError, match="不支持的 prompt_version"):
        response_handler_for_version("unknown")
