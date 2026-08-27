import unittest

from src.audit.audit_logger import AuditLogger
from src.core.data_models import AgentCard, ProtocolType, Task, TaskType
from src.gateway.gateway import Gateway
from src.protocol.local_endpoint import LocalAgentEndpointServer
from src.registry.registry import Registry


class GatewayAgentRuntimeMappingTest(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_passes_selected_agent_id_to_runner(self):
        local = Registry("finance-local")
        global_reg = Registry("global", is_global=True)
        audit = AuditLogger("global")
        calls = []

        def runner(sub_ioa_id, agent_id, prompt):
            calls.append((sub_ioa_id, agent_id, prompt))
            return "ok"

        agent_id = "finance-agent-1"
        server = LocalAgentEndpointServer(
            runner=runner,
            sub_ioa_lookup=lambda aid: "finance" if aid == agent_id else None,
        )
        server.start()
        self.addAsyncCleanup(lambda: server.stop())

        target = AgentCard(
            agent_id=agent_id,
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            actual_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            endpoint=server.endpoint_for(agent_id),
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
            agent_runner=runner,
        )
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="Analyze risk",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioa": "finance"},
        )

        result = await gateway.handle_task(task, requester_id="finance-gw")

        self.assertEqual(calls[0][0], "finance")
        self.assertEqual(calls[0][1], "finance-agent-1")
        self.assertEqual(result.artifacts[0].metadata["selected_agent_id"], "finance-agent-1")
        self.assertEqual(
            result.artifacts[0].metadata["execution_model_scope"],
            "per_agent_llm_runtime",
        )


if __name__ == "__main__":
    unittest.main()
