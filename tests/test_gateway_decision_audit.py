import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.audit.audit_logger import AuditLogger
from src.core.data_models import (
    AgentCard,
    AuditAction,
    GatewayPipelineStage,
    ProtocolType,
    Task,
    TaskStatus,
    TaskType,
)
from src.decision_agents import DeterministicDecisionClient
from src.gateway.gateway import Gateway
from src.registry.registry import Registry


class _DecisionAuditEndpointHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "completed",
            "content": "audited endpoint response",
            "source_agent_id": "finance-agent-1",
        }).encode("utf-8"))

    def log_message(self, format, *args):
        return


class GatewayDecisionAuditTest(unittest.IsolatedAsyncioTestCase):
    async def _build_gateway(self, endpoint: str) -> tuple[Gateway, AuditLogger]:
        local = Registry("finance-local")
        global_reg = Registry("global", is_global=True)
        audit = AuditLogger("global")
        target = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
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
        gateway = Gateway(
            gateway_id="finance-gw",
            sub_ioa_id="finance",
            local_registry=local,
            global_registry=global_reg,
            audit_logger=audit,
            decision_client=DeterministicDecisionClient(),
        )
        return gateway, audit

    async def test_gateway_records_decision_agent_audit_events(self):
        server = HTTPServer(("127.0.0.1", 0), _DecisionAuditEndpointHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/agents/finance-agent-1"
            gateway, audit = await self._build_gateway(endpoint)
            task = Task(
                task_type=TaskType.SINGLE_DOMAIN,
                description="Assess a risky investment",
                required_capabilities=["financial_analysis"],
                payload={"target_sub_ioa": "finance"},
            )

            result = await gateway.handle_task(task, requester_id="finance-gw")

            self.assertEqual(result.status, TaskStatus.COMPLETED)
            entries = await audit.query_chain(task.task_id)
            decision_entries = [
                entry for entry in entries if entry.action == AuditAction.DECISION_AGENT
            ]
            observed_agents = {entry.details["decision_agent"] for entry in decision_entries}
            expected_agents = {
                "TaskUnderstandingAgent",
                "PermissionAnalysisAgent",
                "HumanAgencyAgent",
                "CapabilityMatchingAgent",
                "ProtocolSemanticsAgent",
                "ContentSecurityAgent",
                "ProvenanceVerifierAgent",
                "ConsensusRiskAgent",
            }
            self.assertEqual(observed_agents, expected_agents)
            for entry in decision_entries:
                self.assertIn("stage", entry.details)
                self.assertIn("decision_agent", entry.details)
                self.assertIn("decision", entry.details)
                self.assertIn("confidence", entry.details)

            observed_stages = {entry.details.get("stage") for entry in entries}
            expected_stages = {
                GatewayPipelineStage.TASK_INTAKE.value,
                GatewayPipelineStage.POLICY_ENFORCEMENT.value,
                GatewayPipelineStage.CANDIDATE_RANKING.value,
                GatewayPipelineStage.PROTOCOL_NEGOTIATION.value,
                GatewayPipelineStage.PRE_DELIVERY_SECURITY.value,
                GatewayPipelineStage.HTTP_DELIVERY.value,
                GatewayPipelineStage.POST_DELIVERY_SECURITY.value,
                GatewayPipelineStage.ARTIFACT_AGGREGATION.value,
                GatewayPipelineStage.AUDIT_FINALIZATION.value,
            }
            self.assertTrue(expected_stages.issubset(observed_stages))
            self.assertTrue(all(entry.input_hash for entry in entries))
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
