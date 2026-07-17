import unittest

from src.core.data_models import AgentCard, ProtocolType, Task, TaskStatus, TaskType
from src.experiment.runner import IoAEnvironment


class StructuredArtifactsTest(unittest.IsolatedAsyncioTestCase):
    async def test_single_agent_artifact_has_trace_and_producer(self):
        env = IoAEnvironment({
            "offline_deterministic": True,
            "create_agent_runtimes": False,
            "auto_bind_deterministic_runtimes": True,
        })
        env.add_sub_ioa("finance")
        await env.setup_default_agents()
        result = await env.submit_task(Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="analyze",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioa": "finance"},
        ))
        self.assertEqual(result.status, TaskStatus.COMPLETED)
        artifact = result.artifacts[0]
        self.assertTrue(artifact.producer_agent_id)
        self.assertEqual(artifact.metadata["trace_id"], result.task_id)

    async def test_multi_agent_aggregate_has_contributions(self):
        env = IoAEnvironment({
            "offline_deterministic": True,
            "create_agent_runtimes": False,
            "auto_bind_deterministic_runtimes": True,
        })
        env.add_sub_ioa("finance")
        for agent_id in ("artifact-a", "artifact-b"):
            await env.register_agent(AgentCard(
                agent_id=agent_id,
                display_name=agent_id,
                provider="p",
                sub_ioa_id="finance",
                declared_capabilities=["financial_analysis"],
                supported_protocols=[ProtocolType.A2A],
                certificate=f"cert-{agent_id}",
                reputation_score=0.9,
                permission_scope=["read", "execute"],
            ))
        result = await env.submit_task(Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="analyze",
            required_capabilities=["financial_analysis"],
            payload={"target_sub_ioa": "finance", "enable_multi_agent_orchestration": True, "max_agents": 2},
        ))
        aggregate = result.artifacts[-1]
        self.assertGreaterEqual(len(aggregate.agent_contributions), 2)
        self.assertEqual(aggregate.metadata["trace_id"], result.task_id)


if __name__ == "__main__":
    unittest.main()
