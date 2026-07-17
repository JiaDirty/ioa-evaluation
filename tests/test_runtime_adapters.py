import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.runtime import AgentInvocation, HTTPAgentRuntime, HumanAgentRuntime, LLMAgentRuntime


class RuntimeAdaptersTest(unittest.IsolatedAsyncioTestCase):
    async def test_http_runtime_posts_invocation_and_normalizes_result(self):
        captured = {}

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
                payload = {
                    "status": "completed",
                    "output": {"message": "ok"},
                    "metadata": {"handled_by": "test-server"},
                }
                raw = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format, *args):
                return

        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            runtime = HTTPAgentRuntime("http-agent", f"http://127.0.0.1:{server.server_port}/invoke")
            result = await runtime.invoke(
                AgentInvocation(task_id="t1", trace_id="tr1", agent_id="http-agent", input={"task": "ping"})
            )
        finally:
            server.shutdown()
            thread.join(timeout=2)
            server.server_close()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["message"], "ok")
        self.assertEqual(result.metadata["runtime_type"], "http")
        self.assertEqual(captured["body"]["trace_id"], "tr1")

    async def test_llm_runtime_uses_generate_with_system(self):
        class FakeClient:
            def generate_with_system(self, system, user, **kwargs):
                self.system = system
                self.user = user
                return "llm result"

        client = FakeClient()
        runtime = LLMAgentRuntime("llm-agent", client, system_prompt="system")
        result = await runtime.invoke(
            AgentInvocation(task_id="t2", trace_id="tr2", agent_id="llm-agent", input={"task": "analyze"})
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["text"], "llm result")
        self.assertIn("analyze", client.user)

    async def test_human_runtime_requests_input_or_returns_approval(self):
        runtime = HumanAgentRuntime("human-reviewer")
        pending = await runtime.invoke(
            AgentInvocation(task_id="t3", trace_id="tr3", agent_id="human-reviewer", input={"task": "approve?"})
        )
        self.assertEqual(pending.status, "input_required")
        approved = await runtime.invoke(
            AgentInvocation(
                task_id="t3",
                trace_id="tr3",
                agent_id="human-reviewer",
                metadata={"human_response": "approved"},
            )
        )
        self.assertEqual(approved.status, "completed")
        self.assertTrue(approved.output["approved"])


if __name__ == "__main__":
    unittest.main()
