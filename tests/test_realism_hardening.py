import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.attacks.registry_attack_surface import (
    RegistryAttackSurface,
    RegistryMutationRequest,
)
from src.attacks.routing_manipulation import (
    GatewayRoutingOverride,
    assess_routing_attack_outcome,
    detect_routing_bias,
)
from src.audit.audit_logger import AuditLogger
from src.core.data_models import (
    AgentCard,
    ProtocolMessage,
    ProtocolType,
    Task,
    TaskStatus,
    TaskType,
)
from src.gateway.gateway import Gateway
from src.marketplace.marketplace import TaskMarketplace
from src.protocol.adapters import SemanticMismatchSimulator
from src.registry.registry import Registry


class _EndpointHandler(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        decoded = json.loads(body)
        target = (
            decoded.get("metadata", {}).get("target_agent")
            or decoded.get("context", {}).get("target")
            or decoded.get("receiver")
            or "unknown"
        )
        _EndpointHandler.calls.append({
            "path": self.path,
            "protocol": self.headers.get("X-IoA-Protocol"),
            "target": target,
            "body": decoded,
        })
        self.send_response(200)
        self.end_headers()
        self.wfile.write(json.dumps({
            "status": "completed",
            "content": f"response from {target}",
            "source_agent_id": target,
        }).encode("utf-8"))

    def log_message(self, format, *args):
        return


class RealismHardeningTest(unittest.IsolatedAsyncioTestCase):
    def test_routing_attack_without_allocation_shift_counts_as_neutralized(self):
        outcome = assess_routing_attack_outcome(
            before_counts={"legit-finance": 3},
            after_counts={"legit-finance": 3},
            preferred_agent_id="low-rep-finance",
            judge_attack_succeeded=False,
        )

        self.assertTrue(outcome["passed"])
        self.assertTrue(outcome["route_protected"])
        self.assertEqual(outcome["blocked_checks"], 1)
        self.assertFalse(outcome["routing_bias_verdict"]["manipulation_detected"])

    def test_routing_distribution_overrides_judge_false_positive(self):
        outcome = assess_routing_attack_outcome(
            before_counts={"legit-finance": 3},
            after_counts={"legit-finance": 3},
            preferred_agent_id="low-rep-finance",
            judge_attack_succeeded=True,
        )

        self.assertTrue(outcome["passed"])
        self.assertTrue(outcome["route_protected"])
        self.assertFalse(outcome["judge_confirms_blocked"])

    async def asyncSetUp(self):
        _EndpointHandler.calls = []
        self.server = HTTPServer(("127.0.0.1", 0), _EndpointHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint_base = f"http://127.0.0.1:{self.server.server_port}/agents"

    async def asyncTearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=3)
        self.server.server_close()

    async def _build_gateway(self):
        local = Registry("finance-local")
        global_reg = Registry("global", is_global=True)
        audit = AuditLogger("global")
        gateway = Gateway(
            gateway_id="finance-gw",
            sub_ioa_id="finance",
            local_registry=local,
            global_registry=global_reg,
            audit_logger=audit,
        )
        marketplace = TaskMarketplace()
        marketplace.register_gateway("finance", gateway)

        legit = AgentCard(
            agent_id="legit-finance",
            display_name="Legit Finance",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            endpoint=f"{self.endpoint_base}/legit-finance",
            certificate="cert-legit-finance",
            reputation_score=0.6,
            permission_scope=["read", "execute"],
        )
        await local.register(legit)
        await global_reg.register(legit)
        return local, global_reg, audit, gateway, marketplace

    async def test_identity_spoofing_is_measured_through_marketplace_gateway_chain(self):
        local, global_reg, audit, gateway, marketplace = await self._build_gateway()
        attack_surface = RegistryAttackSurface(
            local_registry=local,
            global_registry=global_reg,
            audit_logger=audit,
            sub_ioa_id="finance",
        )
        fake = AgentCard(
            agent_id="fake-finance",
            display_name="资深金融分析师",
            provider="FinAnalytics Corp",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=[],
            supported_protocols=[ProtocolType.A2A],
            endpoint=f"{self.endpoint_base}/fake-finance",
            certificate="forged_cert_001",
            reputation_score=0.99,
            permission_scope=["read", "execute"],
        )
        outcome = await attack_surface.submit(RegistryMutationRequest(
            operation="register_agent",
            actor_id="external-attacker",
            sub_ioa_id="finance",
            card=fake,
        ))
        self.assertTrue(outcome.applied)
        self.assertFalse(outcome.identity_verified)

        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="请完成金融分析",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioa": "finance"},
        )
        result = await marketplace.execute_task(task)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(result.participating_agents, ["legit-finance"])
        self.assertTrue(all(call["target"] != "fake-finance" for call in _EndpointHandler.calls))
        entries = await audit.query_by_agent("finance-gw")
        stages = [e.details.get("stage") for e in entries]
        self.assertIn("local_discovery", stages)
        self.assertIn("candidate_verification", stages)

    async def test_external_registry_surface_rejects_privileged_field_mutation(self):
        local, global_reg, audit, *_ = await self._build_gateway()
        attack_surface = RegistryAttackSurface(local, global_reg, audit, "finance")

        reputation = await attack_surface.submit(RegistryMutationRequest(
            operation="update_reputation",
            actor_id="external-attacker",
            sub_ioa_id="finance",
            agent_id="legit-finance",
            updates={"reputation_score": 0.99},
        ))
        status = await attack_surface.submit(RegistryMutationRequest(
            operation="update_status",
            actor_id="external-attacker",
            sub_ioa_id="finance",
            agent_id="legit-finance",
            updates={"status": "suspended"},
        ))

        agent = await local.get_agent("legit-finance")
        self.assertFalse(reputation.applied)
        self.assertFalse(status.applied)
        self.assertEqual(agent.reputation_score, 0.6)
        self.assertEqual(agent.status.value, "active")

    async def test_protocol_mismatch_guard_flags_cross_protocol_semantics(self):
        simulator = SemanticMismatchSimulator()
        message = ProtocolMessage(
            source_protocol=ProtocolType.A2A,
            target_protocol=ProtocolType.MCP,
            source_agent_id="finance-gw",
            target_agent_id="mcp-agent",
            method="execute_task",
            params={
                "read-only": True,
                "task": "请读取共享知识，但不要写入",
                "payload": {"attempted_operation": "write_shared_knowledge"},
            },
        )

        findings = simulator.evaluate_message(message)

        self.assertTrue(findings)
        self.assertEqual(findings[0]["field"], "read-only")
        self.assertIn("写操作越权", findings[0]["risk"])

    async def test_malicious_gateway_override_changes_real_routing_distribution(self):
        local, global_reg, _, gateway, marketplace = await self._build_gateway()
        low_rep = AgentCard(
            agent_id="low-rep-finance",
            display_name="Low Rep Finance",
            provider="new-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            endpoint=f"{self.endpoint_base}/low-rep-finance",
            certificate="cert-low-rep-finance",
            reputation_score=0.1,
            permission_scope=["read", "execute"],
        )
        await local.register(low_rep)
        await global_reg.register(low_rep)

        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="请完成金融分析",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioa": "finance"},
        )
        before_result = await marketplace.execute_task(task.model_copy(deep=True))
        before = {before_result.participating_agents[0]: 1}

        with GatewayRoutingOverride(gateway, preferred_agent_id="low-rep-finance"):
            after_result = await marketplace.execute_task(task.model_copy(deep=True))
        after = {after_result.participating_agents[0]: 1}

        verdict = detect_routing_bias(before, after, preferred_agent_id="low-rep-finance")
        self.assertEqual(before_result.participating_agents, ["legit-finance"])
        self.assertEqual(after_result.participating_agents, ["low-rep-finance"])
        self.assertTrue(verdict["manipulation_detected"])


if __name__ == "__main__":
    unittest.main()
