import unittest

from pydantic import BaseModel

from src.decision_agents.base import DecisionAgent, DecisionAgentError
from src.decision_agents.models import DecisionContext


class ToyDecision(BaseModel):
    decision: str
    confidence: float


class FakeClient:
    def __init__(self, response: str):
        self.response = response
        self.calls = []

    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        self.calls.append({"system": system, "user": user, "kwargs": kwargs})
        return self.response


class ToyAgent(DecisionAgent[dict, ToyDecision]):
    name = "ToyAgent"
    output_schema = ToyDecision

    def build_user_prompt(self, decision_input: dict, ctx: DecisionContext) -> str:
        return f"input={decision_input['value']}"


class DecisionAgentTest(unittest.TestCase):
    def test_agent_parses_structured_json_decision(self):
        agent = ToyAgent(FakeClient('{"decision":"allow","confidence":0.9}'))
        result = agent.decide({"value": "x"}, DecisionContext(trace_id="t1", task_id="task-1"))

        self.assertEqual(result.decision, "allow")
        self.assertEqual(result.confidence, 0.9)

    def test_agent_fails_closed_on_invalid_json(self):
        agent = ToyAgent(FakeClient("not json"))
        with self.assertRaises(DecisionAgentError):
            agent.decide({"value": "x"}, DecisionContext(trace_id="t1", task_id="task-1"))


if __name__ == "__main__":
    unittest.main()
