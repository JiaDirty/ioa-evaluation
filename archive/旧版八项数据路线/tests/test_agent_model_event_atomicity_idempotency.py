import asyncio
import pytest

from src.evaluation.agent_model.context_store import AgentContextStore
from src.evaluation.agent_model.event_log import EvaluationEvent


def test_event_retry_is_idempotent_and_payload_tampering_is_detected():
    asyncio.run(_event_retry_case())


async def _event_retry_case():
    store = AgentContextStore()
    await store.open()
    event = EvaluationEvent(
        event_id="ev-1", idempotency_key="call-1", run_id="run-1",
        case_id="CAS-01", variant="risk", event_type="tool_result",
        payload={"semantic_success": True},
    )
    assert store.append_event(event) == "ev-1"
    assert store.append_event(event) == "ev-1"
    assert len(store.list_events("run-1")) == 1
    assert store.verify_event_integrity("run-1") == []
    store.conn.execute(
        "UPDATE evaluation_events SET payload_json='{}' WHERE event_id='ev-1'"
    )
    store.conn.commit()
    assert store.verify_event_integrity("run-1") == ["ev-1"]
    await store.close()


def test_state_and_event_rollback_together_on_injected_crash():
    asyncio.run(_crash_rollback_case())


async def _crash_rollback_case():
    store = AgentContextStore()
    await store.open()
    event = EvaluationEvent(
        event_id="ev-crash", run_id="run-crash", case_id="AGE-01",
        variant="risk", event_type="user_state", payload={"after": "changed"},
    )
    with pytest.raises(RuntimeError, match="injected failure"):
        store.update_run_state_with_event(
            "run-crash", {"user_state": "changed"}, event, inject_failure=True
        )
    assert store.get_run_state("run-crash") is None
    assert store.list_events("run-crash") == []
    await store.close()
