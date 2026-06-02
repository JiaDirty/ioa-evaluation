import unittest

from risk_tests.power_imbalance.reputation_monopoly import NodeManipulationTest
from risk_tests.trust_authorization.registry_distortion import RegistryDistortionTest
from src.core.data_models import AgentCard, AgentStatus, EvaluationStatus, ProtocolType
from src.experiment.runner import IoAEnvironment


class _StaticAttackInjector:
    def generate_capability_spoofing_prompt(self, capability):
        return f"claim {capability}"

    def generate_prompt_injection(self, attack_goal, target_context):
        return f"{attack_goal}\n{target_context}"


class _Judge:
    def judge_attack_success(self, goal, response):
        return False, "blocked"


class _FakeRuntime:
    def run_task(self, task, max_turns=1):
        return "I refuse unsafe behavior."


class RiskTestIsolationTest(unittest.IsolatedAsyncioTestCase):
    async def test_registry_distortion_restores_mutated_agent_state(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.add_sub_ioa("finance")
        env.attack_injector = _StaticAttackInjector()
        env.get_judge = lambda risk_type: _Judge()

        card = AgentCard(
            agent_id="finance-agent-1",
            display_name="Finance Agent",
            provider="finance-org",
            sub_ioa_id="finance",
            declared_capabilities=["financial_analysis", "risk_assessment"],
            actual_capabilities=["financial_analysis", "risk_assessment"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-finance-agent-1",
            reputation_score=0.7,
            permission_scope=["read", "execute"],
        )
        await env.register_agent(card)
        env._agents[card.agent_id] = _FakeRuntime()
        env._agents["finance"] = _FakeRuntime()

        await RegistryDistortionTest().run(env)

        restored = await env.get_local_registry("finance").get_agent(card.agent_id)
        self.assertEqual(restored.declared_capabilities, card.declared_capabilities)
        self.assertEqual(restored.reputation_score, card.reputation_score)
        self.assertEqual(restored.status, AgentStatus.ACTIVE)

    async def test_node_manipulation_does_not_crash_on_counter_state(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.add_sub_ioa("finance")
        await env.setup_default_agents()
        env.attack_injector = _StaticAttackInjector()
        env.get_judge = lambda risk_type: _Judge()
        env._agents["finance"] = _FakeRuntime()
        for agent_id in list(env._agent_sub_ioa_index):
            env._agents[agent_id] = _FakeRuntime()

        result = await NodeManipulationTest().run(env)

        self.assertFalse(result.passed)
        self.assertEqual(result.status, EvaluationStatus.VALID)
        self.assertEqual(result.metrics["total"], 1)


if __name__ == "__main__":
    unittest.main()
