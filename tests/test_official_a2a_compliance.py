import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from src.audit.audit_logger import AuditLogger
from src.core.data_models import AgentCard, Artifact, ProtocolMessage, ProtocolType, TaskResult, TaskStatus
from src.experiment.runner import MetricsEngine
from src.protocol.adapters import A2AAdapter, ProtocolNegotiator
from src.protocol.local_endpoint import LocalAgentEndpointServer


def _post_json(url, payload, headers=None):
    raw = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=raw,
        headers={
            "Content-Type": "application/json",
            "A2A-Version": "1.0",
            **(headers or {}),
        },
        method="POST",
    )
    with urlopen(request, timeout=5) as response:
        return response.status, response.headers, json.loads(response.read().decode("utf-8"))


class _A2ACaptureHandler(BaseHTTPRequestHandler):
    received = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        decoded = json.loads(body)
        _A2ACaptureHandler.received = {
            "path": self.path,
            "content_type": self.headers.get("Content-Type"),
            "a2a_version": self.headers.get("A2A-Version"),
            "ioa_protocol": self.headers.get("X-IoA-Protocol"),
            "authorization": self.headers.get("Authorization"),
            "trace_id": self.headers.get("X-Trace-Id"),
            "body": decoded,
        }
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "jsonrpc": "2.0",
            "id": decoded["id"],
            "result": {
                "task": {
                    "id": "task-123",
                    "contextId": "ctx-123",
                    "status": {"state": "TASK_STATE_COMPLETED"},
                    "artifacts": [{
                        "artifactId": "artifact-123",
                        "parts": [{"text": "official endpoint response"}],
                    }],
                    "metadata": {"sourceAgentId": "finance-agent-1"},
                }
            },
        }).encode("utf-8"))

    def log_message(self, format, *args):
        return


class OfficialA2AComplianceTest(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_sends_official_a2a_v1_jsonrpc_send_message(self):
        server = HTTPServer(("127.0.0.1", 0), _A2ACaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            adapter = A2AAdapter()
            msg = ProtocolMessage(
                source_protocol=ProtocolType.A2A,
                target_protocol=ProtocolType.A2A,
                source_agent_id="finance-gw",
                target_agent_id="finance-agent-1",
                trace_id="trace-123",
                method="execute_task",
                params={"task": "Analyze risk", "payload": {"market": "A股"}},
            )

            result = await adapter.send_message(
                f"http://127.0.0.1:{server.server_port}/rpc",
                msg,
            )
            decoded = adapter.decode_delivery_result(result)

            request = _A2ACaptureHandler.received
            self.assertEqual(request["content_type"], "application/json")
            self.assertEqual(request["a2a_version"], "1.0")
            self.assertEqual(request["ioa_protocol"], "a2a")
            self.assertEqual(request["authorization"], "Bearer testbed-token")
            self.assertEqual(request["trace_id"], "trace-123")
            self.assertEqual(request["body"]["jsonrpc"], "2.0")
            self.assertEqual(request["body"]["method"], "SendMessage")
            self.assertIn("params", request["body"])
            message = request["body"]["params"]["message"]
            self.assertEqual(message["role"], "ROLE_USER")
            self.assertEqual(message["messageId"], msg.message_id)
            self.assertEqual(message["parts"], [{"text": "Analyze risk", "mediaType": "text/plain"}])
            self.assertEqual(message["metadata"]["sourceAgentId"], "finance-gw")
            self.assertEqual(message["metadata"]["targetAgentId"], "finance-agent-1")
            self.assertEqual(message["metadata"]["traceId"], "trace-123")
            self.assertEqual(request["body"]["params"]["metadata"]["payload"], {"market": "A股"})
            self.assertEqual(decoded["status"], "completed")
            self.assertEqual(decoded["content"], "official endpoint response")
            self.assertEqual(decoded["source_agent_id"], "finance-agent-1")
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    async def test_adapter_decodes_official_send_message_request(self):
        raw = json.dumps({
            "jsonrpc": "2.0",
            "id": "msg-123",
            "method": "SendMessage",
            "params": {
                "message": {
                    "messageId": "msg-123",
                    "role": "ROLE_USER",
                    "parts": [{"text": "Plan itinerary", "mediaType": "text/plain"}],
                    "metadata": {
                        "sourceAgentId": "travel-gw",
                        "targetAgentId": "travel-agent-1",
                        "traceId": "trace-456",
                    },
                },
                "metadata": {"payload": {"city": "上海"}},
            },
        })

        message = A2AAdapter().decode(raw)

        self.assertEqual(message.message_id, "msg-123")
        self.assertEqual(message.method, "execute_task")
        self.assertEqual(message.params["task"], "Plan itinerary")
        self.assertEqual(message.params["payload"], {"city": "上海"})
        self.assertEqual(message.source_agent_id, "travel-gw")
        self.assertEqual(message.target_agent_id, "travel-agent-1")
        self.assertEqual(message.trace_id, "trace-456")

    async def test_adapter_builds_official_core_get_and_cancel_requests(self):
        adapter = A2AAdapter()
        get_task = ProtocolMessage(
            source_protocol=ProtocolType.A2A,
            target_protocol=ProtocolType.A2A,
            source_agent_id="finance-gw",
            target_agent_id="finance-agent-1",
            method="GetTask",
            params={"id": "task-123"},
        )
        cancel_task = get_task.model_copy(update={"method": "CancelTask"})

        get_body = json.loads(adapter.encode(get_task))
        cancel_body = json.loads(adapter.encode(cancel_task))

        self.assertEqual(get_body["method"], "GetTask")
        self.assertEqual(get_body["params"], {"id": "task-123"})
        self.assertEqual(cancel_body["method"], "CancelTask")
        self.assertEqual(cancel_body["params"], {"id": "task-123"})

    async def test_local_endpoint_exposes_official_agent_card_and_send_message(self):
        calls = []

        def runner(sub_ioa_id, agent_id, prompt):
            calls.append((sub_ioa_id, agent_id, prompt))
            return "runtime response"

        agent = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            endpoint="",
            permission_scope=["read", "execute"],
        )
        server = LocalAgentEndpointServer(
            runner=runner,
            sub_ioa_lookup=lambda aid: "finance" if aid == agent.agent_id else None,
        )
        agent.endpoint = server.endpoint_for(agent.agent_id)
        server.register_agent_card(agent)
        server.start()
        self.addAsyncCleanup(lambda: server.stop())

        with urlopen(server.agent_card_url_for(agent.agent_id), timeout=5) as response:
            card = json.loads(response.read().decode("utf-8"))

        self.assertEqual(card["name"], "Finance Agent")
        self.assertEqual(card["supportedInterfaces"][0]["protocolBinding"], "JSONRPC")
        self.assertEqual(card["supportedInterfaces"][0]["protocolVersion"], "1.0")
        self.assertEqual(card["defaultInputModes"], ["text/plain", "application/json"])
        self.assertEqual(card["skills"][0]["id"], "financial_analysis")

        adapter = A2AAdapter()
        msg = ProtocolMessage(
            source_protocol=ProtocolType.A2A,
            target_protocol=ProtocolType.A2A,
            source_agent_id="finance-gw",
            target_agent_id=agent.agent_id,
            trace_id="trace-local",
            method="execute_task",
            params={"task": "Analyze liquidity", "payload": {"symbol": "600000"}},
        )
        result = await adapter.send_message(agent.endpoint, msg)
        decoded = adapter.decode_delivery_result(result)

        self.assertEqual(calls[0][0], "finance")
        self.assertEqual(calls[0][1], "finance-agent-1")
        self.assertIn("Analyze liquidity", calls[0][2])
        self.assertEqual(decoded["status"], "completed")
        self.assertEqual(decoded["content"], "runtime response")

    async def test_local_endpoint_supports_http_json_rest_core_methods(self):
        def runner(sub_ioa_id, agent_id, prompt):
            return f"{sub_ioa_id}:{agent_id}:{prompt.splitlines()[-1]}"

        agent = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            endpoint="",
            permission_scope=["read", "execute"],
        )
        server = LocalAgentEndpointServer(
            runner=runner,
            sub_ioa_lookup=lambda aid: "finance" if aid == agent.agent_id else None,
        )
        agent.endpoint = server.endpoint_for(agent.agent_id)
        server.register_agent_card(agent)
        server.start()
        self.addAsyncCleanup(lambda: server.stop())

        status, headers, send_response = _post_json(
            f"{agent.endpoint}/message:send",
            {
                "message": {
                    "messageId": "rest-msg-1",
                    "role": "ROLE_USER",
                    "parts": [{"text": "REST liquidity check", "mediaType": "text/plain"}],
                    "metadata": {
                        "sourceAgentId": "finance-gw",
                        "targetAgentId": agent.agent_id,
                        "traceId": "rest-trace-1",
                    },
                },
                "metadata": {"payload": {"symbol": "600000"}},
            },
            headers={"Content-Type": "application/a2a+json"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(headers.get_content_type(), "application/a2a+json")
        task = send_response["task"]
        self.assertEqual(task["status"]["state"], "TASK_STATE_COMPLETED")
        self.assertEqual(task["metadata"]["sourceAgentId"], agent.agent_id)

        with urlopen(f"{agent.endpoint}/tasks/{task['id']}", timeout=5) as response:
            get_response = json.loads(response.read().decode("utf-8"))
        self.assertEqual(get_response["id"], task["id"])
        self.assertEqual(get_response["contextId"], task["contextId"])

        with self.assertRaises(HTTPError) as ctx:
            _post_json(
                f"{agent.endpoint}/tasks/{task['id']}:cancel",
                {"id": task["id"]},
                headers={"Content-Type": "application/a2a+json"},
            )
        self.assertEqual(ctx.exception.code, 400)
        error = json.loads(ctx.exception.read().decode("utf-8"))["error"]
        self.assertEqual(error["details"][0]["reason"], "TASK_NOT_CANCELABLE")

    async def test_local_endpoint_rejects_unsupported_a2a_version_and_optional_methods(self):
        agent = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            endpoint="",
            permission_scope=["read", "execute"],
        )
        server = LocalAgentEndpointServer(
            runner=lambda sub_ioa_id, agent_id, prompt: "ok",
            sub_ioa_lookup=lambda aid: "finance" if aid == agent.agent_id else None,
        )
        agent.endpoint = server.endpoint_for(agent.agent_id)
        server.register_agent_card(agent)
        server.start()
        self.addAsyncCleanup(lambda: server.stop())

        with self.assertRaises(HTTPError) as version_ctx:
            _post_json(
                agent.endpoint,
                {
                    "jsonrpc": "2.0",
                    "id": "bad-version",
                    "method": "SendMessage",
                    "params": {"message": {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "x"}]}},
                },
                headers={"A2A-Version": "9.9"},
            )
        version_error = json.loads(version_ctx.exception.read().decode("utf-8"))
        self.assertEqual(version_ctx.exception.code, 400)
        self.assertEqual(version_error["error"]["code"], -32009)
        self.assertEqual(version_error["error"]["data"][0]["reason"], "VERSION_NOT_SUPPORTED")

        with self.assertRaises(HTTPError) as stream_ctx:
            _post_json(
                agent.endpoint,
                {
                    "jsonrpc": "2.0",
                    "id": "stream",
                    "method": "SendStreamingMessage",
                    "params": {"message": {"messageId": "m", "role": "ROLE_USER", "parts": [{"text": "x"}]}},
                },
            )
        stream_error = json.loads(stream_ctx.exception.read().decode("utf-8"))
        self.assertEqual(stream_error["error"]["code"], -32004)
        self.assertEqual(stream_error["error"]["data"][0]["reason"], "UNSUPPORTED_OPERATION")

        with self.assertRaises(HTTPError) as push_ctx:
            _post_json(
                agent.endpoint,
                {
                    "jsonrpc": "2.0",
                    "id": "push",
                    "method": "CreateTaskPushNotificationConfig",
                    "params": {"taskId": "task-1"},
                },
            )
        push_error = json.loads(push_ctx.exception.read().decode("utf-8"))
        self.assertEqual(push_error["error"]["code"], -32003)
        self.assertEqual(push_error["error"]["data"][0]["reason"], "PUSH_NOTIFICATION_NOT_SUPPORTED")

    async def test_report_summarizes_official_a2a_compliance_evidence(self):
        artifact = Artifact(
            content="ok",
            source_agent_id="finance-agent-1",
            metadata={
                "execution_transport": "protocol_http_endpoint",
                "a2a_compliance": "official_v1_core_jsonrpc",
                "delivery": {
                    "protocol": "a2a",
                    "a2a_task_id": "task-1",
                    "a2a_context_id": "ctx-1",
                },
            },
        )
        task_result = TaskResult(
            task_id="ioa-task-1",
            status=TaskStatus.COMPLETED,
            artifacts=[artifact],
        )

        report = await MetricsEngine(AuditLogger("global")).generate_report([], [task_result])

        compliance = report["summary"]["a2a_compliance"]
        self.assertEqual(compliance["protocol_http_endpoint_tasks"], 1)
        self.assertEqual(compliance["a2a_tasks"], 1)
        self.assertEqual(compliance["official_v1_core_jsonrpc_tasks"], 1)
        self.assertTrue(compliance["all_a2a_endpoint_tasks_official_core"])
        self.assertEqual(compliance["evidence_task_ids"], ["ioa-task-1"])

    async def test_protocol_negotiator_prefers_official_a2a_when_security_ties(self):
        result = await ProtocolNegotiator().negotiate(
            [ProtocolType.A2A, ProtocolType.MCP, ProtocolType.PRIVATE_API],
            [ProtocolType.A2A, ProtocolType.MCP],
        )

        self.assertTrue(result.success)
        self.assertEqual(result.agreed_protocol, ProtocolType.A2A)


if __name__ == "__main__":
    unittest.main()
