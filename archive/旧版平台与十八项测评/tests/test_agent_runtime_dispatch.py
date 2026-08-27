import unittest

from src.core.data_models import AgentCard, ProtocolType
from src.experiment.runner import IoAEnvironment


class _FakeRuntime:
    def __init__(self, label):
        self.label = label

    def run_task(self, task, max_turns=1):
        return f"{self.label}: {task}"


class AgentRuntimeDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_registered_agent_card_dispatches_to_matching_runtime(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.add_sub_ioa("finance")
        card = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-finance-agent-1",
            permission_scope=["read", "execute"],
        )
        await env.register_agent(card)
        env._agents["finance-agent-1"] = _FakeRuntime("agent-runtime")

        response = env.run_agent_task("finance", "finance-agent-1", "task body")

        self.assertEqual(response, "agent-runtime: task body")

    async def test_registered_agent_card_rejects_wrong_sub_ioa_dispatch(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.add_sub_ioa("finance")
        env.add_sub_ioa("news")
        card = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-finance-agent-1",
            permission_scope=["read", "execute"],
        )
        await env.register_agent(card)
        env._agents["finance-agent-1"] = _FakeRuntime("agent-runtime")

        with self.assertRaises(ValueError):
            env.run_agent_task("news", "finance-agent-1", "task body")


if __name__ == "__main__":
    unittest.main()
