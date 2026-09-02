import pytest

from src.evaluation.scenario_generation.pipeline_api import (
    LiveAPINotAllowedError,
    PipelineAPI,
    StageCallConfig,
)


def test_live_api_is_opt_in_before_client_creation():
    called = False

    def factory(_config):
        nonlocal called
        called = True
        raise AssertionError("client must not be created in offline mode")

    api = PipelineAPI(client_factory=factory)
    with pytest.raises(LiveAPINotAllowedError):
        api.generate_kernel(
            task_card={"category": "判断让渡"},
            prompt="prompt",
            candidate_uid="jud-test-001",
            config=StageCallConfig(model_id="gpt-5.6-sol"),
            output_dir=".",
        )
    assert called is False


def test_stage_call_config_has_deterministic_defaults():
    config = StageCallConfig(model_id="gpt-5.6-sol")
    assert config.temperature == 0.3
    assert config.max_completion_tokens == 16384
