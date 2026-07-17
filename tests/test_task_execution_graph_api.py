import unittest

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state


class TaskExecutionGraphApiTest(unittest.TestCase):
    def setUp(self):
        reset_state()
        self.client = TestClient(app)

    def test_task_execution_graph_endpoint(self):
        response = self.client.post("/api/tasks", json={
            "description": "构建执行图",
            "origin_sub_ioa": "finance",
            "target_sub_ioas": ["finance"],
            "required_capabilities": ["financial_analysis"],
            "payload": {"enable_multi_agent_orchestration": True, "max_agents": 2},
        })
        self.assertEqual(response.status_code, 200)
        task_id = response.json()["task_id"]
        graph = self.client.get(f"/api/tasks/{task_id}/execution-graph")
        self.assertEqual(graph.status_code, 200)
        data = graph.json()
        self.assertEqual(data["task_id"], task_id)
        self.assertGreaterEqual(len(data["nodes"]), 2)
        self.assertIn("edges", data)


if __name__ == "__main__":
    unittest.main()
