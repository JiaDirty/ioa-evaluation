import unittest

from src.experiment.runner import IoAEnvironment
from src.experiment.scenario_loader import ScenarioLoader


class ScenarioGatewayAuthTest(unittest.IsolatedAsyncioTestCase):
    async def test_scenario_registers_gateways_for_cross_domain_authorization(self):
        scenario = ScenarioLoader("data/seeds/seed_006_judgment_surrender.json").load()
        env = IoAEnvironment({"create_agent_runtimes": False})

        await env.setup_from_scenario(scenario)
        task = env.build_task_from_scenario(scenario)

        target_gateway = env.get_gateway("news")
        self.assertIsNotNone(target_gateway)

        auth_result = await target_gateway._check_authorization("finance-gw", task)

        self.assertTrue(auth_result.authorized)
