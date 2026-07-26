import json
import tempfile
import unittest
from pathlib import Path

from src.evaluation.agent_model.context_store import AgentContextStore
from src.evaluation.agent_model.event_log import EvaluationEvent
from src.evaluation.agent_model.trace_export import export_execution_trace


class AgentModelTraceExportTest(unittest.IsolatedAsyncioTestCase):
    async def test_exports_readable_redacted_step_trace(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            db_path = root / "context.db"
            store = AgentContextStore(db_path)
            await store.open()
            session_id = store.upsert_session(
                "run-1", "CAS-01", "risk", "verification_role", "agent-7"
            )
            store.append_turn(
                session_id,
                1,
                input_json={
                    "task_text": "核验受控信息",
                    "selected_agent_ids": ["agent-7"],
                    "model_requests": [{
                        "messages": [{"role": "user", "content": "Bearer abcdefghijklmnop"}]
                    }],
                },
                output_json={"step_output": {"answer": "ok"}},
                tool_calls_json=[{"tool_id": "authoritative_fact_lookup"}],
                artifact_refs_json=["artifact-1"],
            )
            store.append_event(EvaluationEvent(
                event_id="event-model-1",
                run_id="run-1",
                case_id="CAS-01",
                variant="risk",
                role_id="verification_role",
                round_index=1,
                event_type="model_call",
                payload={
                    "agent_id": "agent-7",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 4,
                        "total_tokens": 14,
                    },
                    "latency_ms": 25.0,
                    "retry_count": 1,
                },
            ))
            await store.close()

            exported = export_execution_trace(
                db_path, root, suite_run_id="suite-1"
            )

            self.assertEqual(exported["record_count"], 1)
            self.assertEqual(exported["usage"]["total_tokens"], 14)
            for name in (
                "execution_trace.jsonl",
                "execution_trace.md",
                "execution_trace.html",
                "trace_summary.json",
            ):
                self.assertTrue((root / name).exists())
            jsonl = (root / "execution_trace.jsonl").read_text(encoding="utf-8")
            self.assertIn("[REDACTED]", jsonl)
            self.assertNotIn("abcdefghijklmnop", jsonl)
            first_record = json.loads(jsonl.splitlines()[0])
            self.assertEqual(first_record["model_call_count"], 1)
            html = (root / "execution_trace.html").read_text(encoding="utf-8")
            self.assertIn("agent-7", html)
            self.assertIn("筛选案例", html)


if __name__ == "__main__":
    unittest.main()
