import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state
from src.audit.event_bus import EventBus
from src.persistence import SQLiteDatabase, SQLiteEventStore, SQLiteObservabilityStore
from src.security import REDACTED_VALUE


class ObservabilityPersistenceTest(unittest.TestCase):
    def test_sequence_parent_span_redaction_and_restart_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = SQLiteDatabase(Path(tmp) / "observability.sqlite3")
            observations = SQLiteObservabilityStore(db)
            bus = EventBus(SQLiteEventStore(db), observations)
            parent = bus.start_span(
                task_id="task-1",
                trace_id="trace-1",
                stage="planning",
                event_type="planning_started",
                actor_type="planner",
                actor_id="planner-1",
                input={"api_key": "secret", "goal": "demo"},
            )
            child = bus.start_span(
                task_id="task-1",
                trace_id="trace-1",
                stage="agent_runtime",
                event_type="agent_runtime_started",
                actor_type="agent_runtime",
                actor_id="agent-1",
                parent_span_id=parent.span_id,
            )
            bus.finish_span(
                span_id=child.span_id,
                task_id="task-1",
                trace_id="trace-1",
                stage="agent_runtime",
                event_type="agent_runtime_completed",
                actor_type="agent_runtime",
                actor_id="agent-1",
                output={"answer": "ok"},
            )

            events = bus.query(task_id="task-1")
            self.assertEqual([event.sequence for event in events], sorted(event.sequence for event in events))
            self.assertEqual(child.parent_span_id, parent.span_id)
            self.assertEqual(events[0].input["api_key"], REDACTED_VALUE)

            restarted = SQLiteObservabilityStore(SQLiteDatabase(Path(tmp) / "observability.sqlite3"))
            spans = restarted.list_spans(task_id="task-1")
            child_span = next(span for span in spans if span.span_id == child.span_id)
            self.assertEqual(child_span.status, "completed")
            self.assertEqual(child_span.parent_span_id, parent.span_id)
            self.assertIsNotNone(child_span.duration_ms)
            payloads = restarted.list_payloads(parent.span_id)
            self.assertEqual(payloads[0]["content"]["api_key"], REDACTED_VALUE)


class ObservabilityApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def _create_task(self) -> str:
        response = self.client.post("/api/tasks", json={
            "prompt": "Assess a short travel plan and preserve sources.",
            "execution_mode": "offline_deterministic",
        })
        self.assertEqual(response.status_code, 200)
        return response.json()["task_id"]

    def test_system_graph_and_task_observability(self):
        system = self.client.get("/api/system/graph")
        self.assertEqual(system.status_code, 200)
        node_types = {node["type"] for node in system.json()["nodes"]}
        self.assertTrue({"sub_ioa", "gateway", "registry", "agent", "tool", "judge"}.issubset(node_types))

        task_id = self._create_task()
        observation = self.client.get(f"/api/tasks/{task_id}/observability")
        self.assertEqual(observation.status_code, 200)
        data = observation.json()
        self.assertGreater(len(data["events"]), 0)
        self.assertGreater(len(data["spans"]), 0)
        self.assertGreater(len(data["execution_graph"]["nodes"]), 0)
        self.assertTrue(any(span["input"] or span["output"] for span in data["spans"]))

        span_id = data["spans"][0]["span_id"]
        detail = self.client.get(f"/api/tasks/{task_id}/spans/{span_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["span"]["task_id"], task_id)

    def test_completed_task_websocket_replays_events_without_duplicates(self):
        task_id = self._create_task()
        event_ids = []
        with self.client.websocket_connect(f"/api/tasks/{task_id}/stream?after_sequence=0") as socket:
            while True:
                message = socket.receive_json()
                if message["type"] == "event":
                    event_ids.append(message["event"]["event_id"])
                if message["type"] == "complete":
                    break
        self.assertGreater(len(event_ids), 0)
        self.assertEqual(len(event_ids), len(set(event_ids)))


if __name__ == "__main__":
    unittest.main()
