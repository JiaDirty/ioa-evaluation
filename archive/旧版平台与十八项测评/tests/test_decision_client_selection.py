import unittest
from unittest.mock import patch

from src.decision_agents import DeterministicDecisionClient
from src.experiment.runner import IoAEnvironment


class DecisionClientSelectionTest(unittest.TestCase):
    def test_offline_environment_uses_deterministic_decision_client(self):
        env = IoAEnvironment({"create_agent_runtimes": False})

        self.assertIsInstance(env._decision_client, DeterministicDecisionClient)

    def test_live_environment_uses_configured_llm_decision_client(self):
        live_client = object()
        with patch("src.experiment.runner.get_agent_llm_client", return_value=live_client):
            env = IoAEnvironment({"create_agent_runtimes": True})

        self.assertIs(env._decision_client, live_client)


if __name__ == "__main__":
    unittest.main()
