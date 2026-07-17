import unittest

from src.core.data_models import AgentCard, Task, TaskType
from src.orchestration import SimpleOrchestrationPlanner, StepStatus


class ExecutionGraphTest(unittest.TestCase):
    def test_plan_converts_to_execution_graph(self):
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="graph",
            payload={"enable_multi_agent_orchestration": True, "max_agents": 2},
        )
        plan = SimpleOrchestrationPlanner().build_plan(task, [
            AgentCard(agent_id="a1", display_name="A1", provider="p", sub_ioa_id="finance"),
            AgentCard(agent_id="a2", display_name="A2", provider="p", sub_ioa_id="finance"),
        ])
        graph = plan.to_execution_graph()
        self.assertEqual(graph.task_id, task.task_id)
        self.assertEqual(len([node for node in graph.nodes if node.node_type == "agent"]), 2)
        self.assertTrue(any(node.node_type == "aggregate" for node in graph.nodes))
        self.assertTrue(all(node.status == StepStatus.PENDING for node in graph.nodes))
        self.assertGreater(len(graph.edges), 0)


if __name__ == "__main__":
    unittest.main()
