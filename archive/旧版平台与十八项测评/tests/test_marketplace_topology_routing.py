import unittest

from src.core.data_models import Task, TaskStatus, TaskType
from src.experiment.runner import IoAEnvironment


class _FakeRuntime:
    def __init__(self, label):
        self.label = label

    def run_task(self, task, max_turns=1):
        return f"{self.label}: {task}"


class MarketplaceTopologyRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_cross_domain_task_fails_when_target_is_not_topology_reachable(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.add_sub_ioa("finance")
        env.add_sub_ioa("news")
        await env.setup_default_agents()
        for agent_id, sub_ioa_id in list(env._agent_sub_ioa_index.items()):
            env._agents[agent_id] = _FakeRuntime(agent_id)

        task = Task(
            task_type=TaskType.CROSS_DOMAIN,
            description="Cross-domain analysis",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioas": ["finance", "news"]},
        )

        result = await env.submit_task(task)

        self.assertEqual(result.status, TaskStatus.FAILED)
        self.assertIn("not reachable", result.error)

    async def test_cross_domain_task_uses_target_specific_capabilities(self):
        env = IoAEnvironment({"create_agent_runtimes": False})
        env.add_sub_ioa("finance")
        env.add_sub_ioa("news")
        env.topology.add_edge("finance", "news")
        await env.setup_default_agents()
        for agent_id, sub_ioa_id in list(env._agent_sub_ioa_index.items()):
            env._agents[agent_id] = _FakeRuntime(agent_id)

        task = Task(
            task_type=TaskType.CROSS_DOMAIN,
            description="Finance and news analysis",
            payload={
                "target_sub_ioas": ["finance", "news"],
                "required_capabilities_by_sub_ioa": {
                    "finance": ["financial_analysis"],
                    "news": ["news_aggregation"],
                },
            },
        )

        result = await env.submit_task(task)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertEqual(len(result.artifacts), 2)
        self.assertTrue(
            any("news" in artifact.metadata["execution_sub_ioa_id"] for artifact in result.artifacts)
        )


if __name__ == "__main__":
    unittest.main()
