import unittest

from src.evaluation.agent_model.categories.consensus import (
    _assessment_target,
)
from src.evaluation.agent_model.prompt_policy import visible_action_schema


class AgentModelConsensusTargetTest(unittest.TestCase):
    def test_target_contains_exact_identifier_and_current_statement(self):
        statement = "受控案例中的当前待判断说法"

        target = _assessment_target(statement)

        self.assertEqual(target, {
            "claim_id": "说法-01",
            "statement": statement,
        })
        schema = visible_action_schema(
            "CON", [], required_claim_id="说法-01"
        )
        self.assertEqual(set(schema["properties"]), {
            "status", "decision", "answer", "evidence_refs",
            "next_action", "handoff_message",
        })
        self.assertNotIn("AgentBehaviorRecord", schema.get("$defs", {}))
        self.assertNotIn("说法-01", str(schema))


if __name__ == "__main__":
    unittest.main()
