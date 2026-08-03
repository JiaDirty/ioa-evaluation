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
        claims = schema["$defs"]["AgentBehaviorRecord"]["properties"]["claims"]
        claim = schema["$defs"]["AgentClaimRecord"]["properties"]["claim_id"]
        self.assertEqual(claims["minItems"], 1)
        self.assertEqual(claims["maxItems"], 1)
        self.assertEqual(claim["enum"], ["说法-01"])


if __name__ == "__main__":
    unittest.main()
