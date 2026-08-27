import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state


class TaskApiPersistenceTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_task_detail_reads_persistent_events_and_artifacts_after_env_rebuild(self):
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
        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]

        reset_state(clear_persistence=False)
        rebuilt = TestClient(app)
        detail = rebuilt.get(f"/api/tasks/{task_id}/detail")

        self.assertEqual(detail.status_code, 200)
        data = detail.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertGreater(len(data["events"]), 0)
        self.assertGreater(len(data["artifacts"]), 0)
        self.assertEqual(rebuilt.get(f"/api/tasks/{task_id}/artifacts").status_code, 200)


if __name__ == "__main__":
    unittest.main()
