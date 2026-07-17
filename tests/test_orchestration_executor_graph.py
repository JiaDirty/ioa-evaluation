import unittest

from src.orchestration import ExecutionGraph, ExecutionNode, OrchestrationExecutor, StepStatus


class OrchestrationExecutorGraphTest(unittest.IsolatedAsyncioTestCase):
    async def test_execute_graph_runs_dependencies(self):
        graph = ExecutionGraph(
            task_id="task",
            trace_id="trace",
            nodes=[
                ExecutionNode(node_id="a", node_type="agent", label="A", target_id="agent-a"),
                ExecutionNode(node_id="b", node_type="aggregate", label="B", depends_on=["a"]),
            ],
        )
        graph.refresh_edges()

        async def runner(node: ExecutionNode):
            return {"node": node.node_id}

        result = await OrchestrationExecutor().execute_graph(graph, runner)
        self.assertTrue(all(node.status == StepStatus.COMPLETED for node in result.nodes))
        self.assertEqual(result.node_by_id("b").output["node"], "b")


if __name__ == "__main__":
    unittest.main()
