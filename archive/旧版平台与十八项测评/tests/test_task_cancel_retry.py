import time
import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state


class TaskCancelRetryApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_async_task_starts_queued_and_eventually_completes(self):
        response = self.client.post(
            "/api/tasks",
            json={
                "description": "分析短期投资风险",
                "origin_sub_ioa": "finance",
                "target_sub_ioas": ["finance"],
                "required_capabilities": ["financial_analysis"],
                "payload": {},
                "async_mode": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "queued")
        task_id = response.json()["task_id"]

        for _ in range(20):
            status = self.client.get(f"/api/tasks/{task_id}").json()["status"]
            if status == "completed":
                break
            time.sleep(0.05)
        self.assertEqual(self.client.get(f"/api/tasks/{task_id}").json()["status"], "completed")

    def test_queued_task_cancel_and_retry(self):
        response = self.client.post(
            "/api/tasks",
            json={
                "description": "分析短期投资风险",
                "origin_sub_ioa": "finance",
                "target_sub_ioas": ["finance"],
                "required_capabilities": ["financial_analysis"],
                "payload": {},
                "async_mode": True,
            },
        )
        task_id = response.json()["task_id"]
        cancel = self.client.post(f"/api/tasks/{task_id}/cancel")
        self.assertEqual(cancel.status_code, 200)
        self.assertIn(self.client.get(f"/api/tasks/{task_id}").json()["status"], {"cancelled", "cancel_requested"})

        retry = self.client.post(f"/api/tasks/{task_id}/retry", json={"mode": "full"})
        self.assertEqual(retry.status_code, 200)
        self.assertEqual(retry.json()["status"], "queued")

    def test_completed_task_cancel_returns_conflict(self):
        response = self.client.post(
            "/api/tasks",
            json={
                "description": "分析短期投资风险",
                "origin_sub_ioa": "finance",
                "target_sub_ioas": ["finance"],
                "required_capabilities": ["financial_analysis"],
                "payload": {},
            },
        )
        task_id = response.json()["task_id"]
        cancel = self.client.post(f"/api/tasks/{task_id}/cancel")
        self.assertEqual(cancel.status_code, 409)


if __name__ == "__main__":
    unittest.main()
