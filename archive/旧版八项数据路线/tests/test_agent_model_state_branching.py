import unittest

from src.evaluation.agent_model.context_store import AgentContextStore
from src.evaluation.agent_model.event_log import EvaluationEvent


class AgentModelStateBranchingTest(unittest.IsolatedAsyncioTestCase):
    async def test_run_state_update_keeps_json_and_status_column_in_sync(self):
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            store.update_run_state("run-1", {
                "case_id": "CAS-01",
                "risk_type": "cascade_propagation",
                "variant": "baseline",
                "status": "running",
            })
            store.update_run_state("run-1", {"status": "completed"})
            state = store.get_run_state("run-1")
            stored = store.list_run_states("run-1")[0]
        finally:
            await store.close()

        self.assertEqual(state["status"], "completed")
        self.assertEqual(stored["status"], "completed")
        self.assertEqual(stored["stored_status"], "completed")
        self.assertEqual(stored["case_id"], "CAS-01")

    async def test_snapshot_is_immutable_and_recovery_is_a_copy(self):
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            store.update_run_state("risk-run", {
                "case_id": "CAS-01",
                "risk_type": "cascade_propagation",
                "variant": "risk",
                "status": "completed",
                "shared_value": {"contaminated": True},
            })
            store.append_event(EvaluationEvent(
                event_id="risk-event",
                run_id="risk-run",
                case_id="CAS-01",
                variant="risk",
                event_type="artifact",
                payload={"artifact_id": "bad-artifact"},
            ))
            store.create_scenario_snapshot(
                snapshot_id="snapshot-risk",
                scenario_state_id="state-risk",
                source_run_id="risk-run",
                case_id="CAS-01",
                repeat_index=0,
            )
            store.initialize_run_from_snapshot(
                run_id="recovery-run",
                snapshot_id="snapshot-risk",
                variant="recovery",
            )
            store.update_run_state("recovery-run", {
                "shared_value": {"contaminated": False},
            })

            snapshot = store.get_scenario_snapshot("snapshot-risk")
            recovery = store.get_run_state("recovery-run")
        finally:
            await store.close()

        self.assertTrue(snapshot["state"]["shared_value"]["contaminated"])
        self.assertEqual(snapshot["event_ids"], ["risk-event"])
        self.assertFalse(recovery["shared_value"]["contaminated"])
        self.assertEqual(recovery["parent_snapshot_id"], "snapshot-risk")

    async def test_snapshot_id_cannot_be_overwritten(self):
        store = AgentContextStore(":memory:")
        await store.open()
        try:
            store.update_run_state("risk-run", {
                "case_id": "CAS-01",
                "risk_type": "cascade_propagation",
                "variant": "risk",
            })
            kwargs = dict(
                snapshot_id="snapshot-risk",
                scenario_state_id="state-risk",
                source_run_id="risk-run",
                case_id="CAS-01",
                repeat_index=0,
            )
            store.create_scenario_snapshot(**kwargs)
            with self.assertRaisesRegex(ValueError, "already exists"):
                store.create_scenario_snapshot(**kwargs)
        finally:
            await store.close()
