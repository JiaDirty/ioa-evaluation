import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state


class TaskApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_create_get_events_and_invalid_origin(self):
        response = self.client.post("/api/tasks", json={
            "description": "分析短期投资风险",
            "origin_sub_ioa": "finance",
            "target_sub_ioas": ["finance"],
            "required_capabilities": ["financial_analysis"],
            "payload": {},
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("task_id", data)
        self.assertEqual(self.client.get(f"/api/tasks/{data['task_id']}").status_code, 200)
        detail = self.client.get(f"/api/tasks/{data['task_id']}/detail")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["response"]["task_id"], data["task_id"])
        events = self.client.get(f"/api/tasks/{data['task_id']}/events")
        self.assertEqual(events.status_code, 200)
        self.assertGreater(len(events.json()), 0)
        invalid = self.client.post("/api/tasks", json={
            "description": "x",
            "origin_sub_ioa": "missing",
        })
        self.assertEqual(invalid.status_code, 404)


if __name__ == "__main__":
    unittest.main()
