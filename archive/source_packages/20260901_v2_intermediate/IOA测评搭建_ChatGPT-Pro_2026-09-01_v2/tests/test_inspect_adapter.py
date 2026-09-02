import json
from pathlib import Path

from inspect_ai import eval as inspect_eval
from inspect_ai.log import read_eval_log
from inspect_ai.model import ModelOutput, get_model

from src.evaluation.business_protocol.dataset import load_evaluation_dataset
from src.evaluation.inspect_adapter import (
    RESULT_STORE_KEY,
    build_inspect_samples,
    build_inspect_task,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = PROJECT_ROOT / "data" / "scenarios"

FINAL_RESULT = json.dumps(
    {
        "status": "COMPLETED",
        "decision": "完成当前业务判断。",
        "answer": "已依据当前可见记录完成处理。",
        "evidence_refs": [],
        "next_action": "无",
        "handoff_message": "当前步骤已经完成。",
        "decision_basis": "依据当前可见记录和工具返回信息。",
    },
    ensure_ascii=False,
)


def _legacy_dataset():
    return load_evaluation_dataset(
        [SCENARIO_DIR],
        profile="legacy_reference",
        require_complete_legacy=True,
    )


def test_builds_one_sample_per_complete_paired_scenario() -> None:
    dataset = _legacy_dataset()

    samples = build_inspect_samples(dataset)

    assert len(samples) == 11
    assert {str(sample.id) for sample in samples} == set(dataset.cases)
    for sample in samples:
        assert "安全行为" not in str(sample.input)
        assert "危险行为" not in str(sample.input)
        assert "scoring_contract" not in str(sample.input)
        assert sample.metadata is not None
        assert sample.metadata["case_id"] == sample.id
        assert len(sample.metadata["case_fingerprint"]) == 64


def test_rejects_unknown_or_duplicate_case_selection() -> None:
    dataset = _legacy_dataset()

    try:
        build_inspect_samples(dataset, case_ids=["missing-case"])
    except ValueError as exc:
        assert "unknown case IDs" in str(exc)
    else:  # pragma: no cover - explicit assertion message is clearer than pytest.raises
        raise AssertionError("unknown case ID was accepted")

    case_id = next(iter(dataset.cases))
    try:
        build_inspect_samples(dataset, case_ids=[case_id, case_id])
    except ValueError as exc:
        assert "must be unique" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("duplicate case IDs were accepted")


def test_inspect_provider_path_keeps_oracle_hidden_and_stores_paired_result(
    tmp_path: Path,
) -> None:
    dataset = _legacy_dataset()
    case_id = next(iter(dataset.cases))
    observed_requests: list[dict[str, object]] = []

    def model_output(messages, tools, tool_choice, config):
        observed_requests.append(
            {
                "messages": [message.model_dump(mode="json") for message in messages],
                "tools": [tool.model_dump(mode="json") for tool in tools],
                "tool_choice": tool_choice,
                "response_schema": (
                    config.response_schema.model_dump(mode="json")
                    if config.response_schema is not None
                    else None
                ),
            }
        )
        return ModelOutput.from_content(model="mockllm/model", content=FINAL_RESULT)

    task = build_inspect_task(
        dataset,
        case_ids=[case_id],
        run_level="key_node",
        execution_mode="inspect-provider",
    )
    model = get_model("mockllm/model", custom_outputs=model_output)

    log = inspect_eval(
        task,
        model=model,
        log_dir=str(tmp_path),
        display="none",
        fail_on_error=True,
    )[0]
    loaded = read_eval_log(log.location)

    assert loaded.status == "success"
    assert loaded.samples is not None and len(loaded.samples) == 1
    sample = loaded.samples[0]
    assert RESULT_STORE_KEY in sample.store
    paired = sample.store[RESULT_STORE_KEY]
    assert paired["case_id"] == case_id
    assert set(paired) >= {"baseline", "mechanism", "recovery"}
    assert len(observed_requests) >= 2
    model_visible = json.dumps(observed_requests, ensure_ascii=False)
    assert "scoring_contract" not in model_visible
    assert "safe_behaviors" not in model_visible
    assert "unsafe_behaviors" not in model_visible
    assert sample.scores is not None
    score = sample.scores["ioa_protocol_scorer"].value
    assert isinstance(score, dict)
    assert set(score) >= {
        "normal_valid",
        "risk_valid",
        "normal_impact_safe",
        "risk_impact_safe",
        "recovery_effective",
        "risk_causal_delta",
    }
