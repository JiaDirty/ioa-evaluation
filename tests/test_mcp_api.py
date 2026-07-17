import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state


class MCPApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_servers_returns_configured_servers(self):
        response = self.client.get("/api/mcp/servers")
        self.assertEqual(response.status_code, 200)
        server_ids = [server["server_id"] for server in response.json()]
        self.assertIn("local-demo-mcp", server_ids)

    def test_sync_all_disabled_servers_is_noop(self):
        response = self.client.post("/api/mcp/sync-tools")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["synced"], 0)


if __name__ == "__main__":
    unittest.main()
