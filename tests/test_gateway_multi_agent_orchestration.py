import unittest

from src.core.data_models import AgentCard, ProtocolType, Task, TaskStatus, TaskType
from src.experiment.runner import IoAEnvironment


class GatewayMultiAgentOrchestrationTest(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_runs_multi_agent_orchestration(self):
        env = IoAEnvironment({
            "offline_deterministic": True,
            "create_agent_runtimes": False,
            "auto_bind_deterministic_runtimes": True,
        })
        env.add_sub_ioa("finance")
        await env.setup_default_topology("full_mesh")
        for agent_id in ("orch-a", "orch-b"):
            await env.register_agent(AgentCard(
                agent_id=agent_id,
                display_name=agent_id,
                provider="p",
                sub_ioa_id="finance",
                declared_capabilities=["financial_analysis"],
                actual_capabilities=["financial_analysis"],
                supported_protocols=[ProtocolType.A2A],
                certificate=f"cert-{agent_id}",
                reputation_score=0.9,
                permission_scope=["read", "execute"],
            ))
        result = await env.submit_task(Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="compare two finance agents",
            required_capabilities=["financial_analysis"],
            payload={
                "target_sub_ioa": "finance",
                "enable_multi_agent_orchestration": True,
                "max_agents": 2,
            },
        ))
        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertGreaterEqual(len(result.participating_agents), 2)
        self.assertIsInstance(result.output, dict)
        self.assertIn("contributions", result.output)


if __name__ == "__main__":
    unittest.main()
