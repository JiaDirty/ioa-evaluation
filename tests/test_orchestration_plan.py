import unittest

from src.core.data_models import AgentCard, Task, TaskType
from src.orchestration import SimpleOrchestrationPlanner


class OrchestrationPlanTest(unittest.TestCase):
    def test_planner_builds_parallel_plan_when_enabled(self):
        task = Task(
            task_type=TaskType.SINGLE_DOMAIN,
            description="analyze",
            required_capabilities=["financial_analysis"],
            payload={"enable_multi_agent_orchestration": True, "max_agents": 2},
        )
        candidates = [
            AgentCard(agent_id="a1", display_name="A1", provider="p", sub_ioa_id="finance"),
            AgentCard(agent_id="a2", display_name="A2", provider="p", sub_ioa_id="finance"),
        ]
        plan = SimpleOrchestrationPlanner().build_plan(task, candidates)
        self.assertEqual(plan.mode, "parallel")
        self.assertEqual([step.agent_id for step in plan.steps], ["a1", "a2"])


if __name__ == "__main__":
    unittest.main()
