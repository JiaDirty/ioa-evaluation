import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.audit.audit_logger import AuditLogger
from src.core.data_models import AgentCard, ProtocolType, Task, TaskStatus, TaskType
from src.gateway.gateway import Gateway
from src.registry.registry import Registry


class _AgentEndpointHandler(BaseHTTPRequestHandler):
    received = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        decoded = json.loads(body)
        _AgentEndpointHandler.received = {
            "path": self.path,
            "protocol": self.headers.get("X-IoA-Protocol"),
            "body": decoded,
        }
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "completed",
            "content": "endpoint response",
            "source_agent_id": "finance-agent-1",
        }).encode("utf-8"))

    def log_message(self, format, *args):
        return


class GatewayProtocolEndpointDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def _build_gateway(
        self,
        endpoint: str,
        target_protocols: list[ProtocolType] | None = None,
    ) -> Gateway:
        local = Registry("finance-local")
        global_reg = Registry("global", is_global=True)
        target_protocols = target_protocols or [ProtocolType.A2A]
        target = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=["financial_analysis"],
            supported_protocols=target_protocols,
            endpoint=endpoint,
            certificate="cert-finance-agent-1",
            reputation_score=0.9,
            permission_scope=["read", "execute"],
        )
        await local.register(target)
        await global_reg.register(target)
        gateway_card = AgentCard(
            agent_id="finance-gw",
            display_name="Finance Gateway",
            provider="finance-infrastructure",
            sub_ioa_id="finance",
            declared_capabilities=["gateway"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-finance-gw",
            reputation_score=1.0,
            permission_scope=["read", "execute", "relay"],
        )
        await local.register(gateway_card)
        await global_reg.register(gateway_card)
        return Gateway(
            gateway_id="finance-gw",
            sub_ioa_id="finance",
            local_registry=local,
            global_registry=global_reg,
            audit_logger=AuditLogger("global"),
        )

    async def test_gateway_dispatches_via_protocol_http_endpoint(self):
        server = HTTPServer(("127.0.0.1", 0), _AgentEndpointHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/agents/finance-agent-1"
            gateway = await self._build_gateway(endpoint)
            task = Task(
                task_type=TaskType.SINGLE_DOMAIN,
                description="Analyze risk",
                required_capabilities=["financial_analysis"],
                payload={"target_sub_ioa": "finance"},
            )

            result = await gateway.handle_task(task, requester_id="finance-gw")

            self.assertEqual(result.status, TaskStatus.COMPLETED)
            self.assertEqual(result.output, "endpoint response")
            self.assertEqual(_AgentEndpointHandler.received["protocol"], "a2a")
            self.assertEqual(_AgentEndpointHandler.received["body"]["method"], "SendMessage")
            self.assertEqual(
                _AgentEndpointHandler.received["body"]["params"]["message"]["parts"][0]["text"],
                "Analyze risk",
            )
            self.assertEqual(
                result.artifacts[0].metadata["execution_transport"],
                "protocol_http_endpoint",
            )
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    async def test_gateway_prefers_official_a2a_for_multi_protocol_agent(self):
        server = HTTPServer(("127.0.0.1", 0), _AgentEndpointHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _AgentEndpointHandler.received = {}
            endpoint = f"http://127.0.0.1:{server.server_port}/agents/finance-agent-1"
            gateway = await self._build_gateway(
                endpoint,
                target_protocols=[ProtocolType.MCP, ProtocolType.A2A, ProtocolType.PRIVATE_API],
            )
            task = Task(
                task_type=TaskType.SINGLE_DOMAIN,
                description="Analyze risk through the strongest common protocol",
                required_capabilities=["financial_analysis"],
                payload={"target_sub_ioa": "finance"},
            )

            result = await gateway.handle_task(task, requester_id="finance-gw")

            self.assertEqual(result.status, TaskStatus.COMPLETED)
            self.assertEqual(_AgentEndpointHandler.received["protocol"], "a2a")
            self.assertEqual(_AgentEndpointHandler.received["body"]["method"], "SendMessage")
            self.assertEqual(
                result.artifacts[0].metadata["a2a_compliance"],
                "official_v1_core_jsonrpc",
            )
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    async def test_gateway_fails_closed_when_selected_agent_has_no_endpoint(self):
        gateway = await self._build_gateway("")
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Analyze risk",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioa": "finance"},
        )

        result = await gateway.handle_task(task, requester_id="finance-gw")

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("endpoint", result.error.lower())

    async def test_gateway_human_approval_task_fails_before_agent_dispatch(self):
        server = HTTPServer(("127.0.0.1", 0), _AgentEndpointHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            _AgentEndpointHandler.received = {}
            endpoint = f"http://127.0.0.1:{server.server_port}/agents/finance-agent-1"
            gateway = await self._build_gateway(endpoint)
            task = Task(
                task_type=TaskType.SINGLE_DOMAIN,
                description="Execute high impact investment",
                required_capabilities=["financial_analysis"],
                payload={
                    "target_sub_ioa": "finance",
                    "human_approval_required": True,
                },
            )

            result = await gateway.handle_task(task, requester_id="finance-gw")

            self.assertEqual(result.status, TaskStatus.FAILED)
            self.assertIn("Human approval required", result.error)
            self.assertEqual(_AgentEndpointHandler.received, {})
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
