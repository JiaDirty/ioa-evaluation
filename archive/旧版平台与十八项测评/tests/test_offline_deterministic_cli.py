import unittest
from unittest.mock import patch

from run_experiment import build_environment_config, setup_environment
from src.core.data_models import Task, TaskStatus, TaskType
from src.experiment.runner import DeterministicJudge


class _Args:
    offline = False
    offline_deterministic = True
    deterministic = False


class OfflineDeterministicCliTest(unittest.IsolatedAsyncioTestCase):
    async def test_offline_environment_avoids_live_llm_and_uses_http_endpoint(self):
        config = build_environment_config(_Args())

        with (
            patch(
                "src.experiment.runner.get_judge_llm_client",
                side_effect=AssertionError("live decision client should not be used"),
            ),
            patch(
                "src.attacks.attack_injector.get_judge_llm_client",
                side_effect=AssertionError("live attack client should not be used"),
            ),
            patch(
                "src.attacks.llm_judge.get_judge_llm_client",
                side_effect=AssertionError("live judge client should not be used"),
            ),
        ):
            env = await setup_environment(config)
            try:
                self.assertTrue(env.offline_deterministic)
                self.assertFalse(env.create_agent_runtimes)
                self.assertIsInstance(env.get_judge("framework_probe"), DeterministicJudge)

                task = Task(
                    task_type=TaskType.SINGLE_DOMAIN,
                    description="Analyze finance risk with evidence boundaries",
                    required_capabilities=["financial_analysis"],
                    payload={"target_sub_ioa": "finance"},
                )

                result = await env.submit_task(task)

                self.assertEqual(result.status, TaskStatus.COMPLETED)
                self.assertTrue(result.artifacts)
                metadata = result.artifacts[0].metadata
                self.assertEqual(metadata["execution_transport"], "protocol_http_endpoint")
                self.assertEqual(metadata["delivery"]["protocol"], "a2a")
                self.assertEqual(metadata["a2a_compliance"], "official_v1_core_jsonrpc")
            finally:
                if env._local_endpoint_server is not None:
                    env._local_endpoint_server.stop()


if __name__ == "__main__":
    unittest.main()
