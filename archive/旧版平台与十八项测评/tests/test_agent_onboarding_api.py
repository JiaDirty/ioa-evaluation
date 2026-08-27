import asyncio
import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import get_ioa_env, reset_state
from src.core.data_models import DiscoveryQuery


class AgentOnboardingApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_onboard_verify_suspend_activate(self):
        card = {
            "agent_id": "onboard-test-agent",
            "display_name": "Onboard Test Agent",
            "provider": "test",
            "sub_ioa_id": "finance",
            "declared_capabilities": ["financial_analysis"],
            "supported_protocols": ["a2a"],
            "endpoint": "http://127.0.0.1:9999/agents/onboard-test-agent",
            "permission_scope": ["read", "execute"],
        }
        onboard = self.client.post("/api/agents/onboard", json=card)
        self.assertEqual(onboard.status_code, 200)
        self.assertEqual(onboard.json()["status"], "suspended")
        registry = self.client.get("/api/agents/registry?include_inactive=true")
        self.assertEqual(registry.status_code, 200)
        self.assertIn("onboard-test-agent", [agent["agent_id"] for agent in registry.json()])
        active_only = self.client.get("/api/agents/registry?include_inactive=false")
        self.assertNotIn("onboard-test-agent", [agent["agent_id"] for agent in active_only.json()])
        self.assertEqual(self.client.post("/api/agents/onboard", json={**card, "agent_id": "bad", "endpoint": ""}).status_code, 400)
        self.assertEqual(self.client.post("/api/agents/onboard-test-agent/verify").status_code, 200)
        self.assertEqual(self.client.post("/api/agents/onboard-test-agent/activate").json()["status"], "active")
        env = asyncio.run(get_ioa_env())
        discovered = asyncio.run(env.global_registry.discover(DiscoveryQuery(required_capabilities=["financial_analysis"])))
        self.assertIn("onboard-test-agent", [agent.agent_id for agent in discovered])
        self.assertEqual(self.client.post("/api/agents/onboard-test-agent/suspend").json()["status"], "suspended")


if __name__ == "__main__":
    unittest.main()
