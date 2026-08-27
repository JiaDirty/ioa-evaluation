import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state


class ToolApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_list_detail_denied_call_and_history(self):
        tools = self.client.get("/api/tools")
        self.assertEqual(tools.status_code, 200)
        self.assertGreaterEqual(len(tools.json()), 8)

        detail = self.client.get("/api/tools/get_stock_price")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["tool_id"], "get_stock_price")

        history = self.client.get("/api/tools/history")
        self.assertEqual(history.status_code, 200)
        self.assertEqual(history.json(), [])

        denied = self.client.post(
            "/api/tools/get_stock_price/call",
            json={"arguments": {"ticker": "AAPL"}, "granted_scopes": []},
        )
        self.assertEqual(denied.status_code, 200)
        self.assertEqual(denied.json()["status"], "denied")

        history = self.client.get("/api/tools/history")
        self.assertEqual(len(history.json()), 1)

        missing = self.client.get("/api/tools/missing-tool")
        self.assertEqual(missing.status_code, 404)


if __name__ == "__main__":
    unittest.main()
