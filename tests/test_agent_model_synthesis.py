import unittest

from src.core.data_models import Artifact, TaskSpec
from src.decision_agents.synthesis import SynthesisAgent


class _BrokenClient:
    def generate(self, _prompt):
        raise RuntimeError("provider unavailable")


class AgentModelSynthesisTest(unittest.TestCase):
    def setUp(self):
        self.task_spec = TaskSpec(normalized_goal="combine evidence")
        self.artifacts = [Artifact(content={"text": "evidence"})]

    def test_formal_mode_requires_model_client(self):
        agent = SynthesisAgent(allow_deterministic_fallback=False)

        with self.assertRaisesRegex(RuntimeError, "requires a synthesis model"):
            agent.synthesize(task_spec=self.task_spec, artifacts=self.artifacts)

    def test_formal_mode_does_not_hide_provider_failure(self):
        agent = SynthesisAgent(
            _BrokenClient(),
            allow_deterministic_fallback=False,
        )

        with self.assertRaisesRegex(RuntimeError, "LLM synthesis failed"):
            agent.synthesize(task_spec=self.task_spec, artifacts=self.artifacts)

    def test_legacy_mode_keeps_deterministic_compatibility(self):
        decision = SynthesisAgent().synthesize(
            task_spec=self.task_spec,
            artifacts=self.artifacts,
        )

        self.assertIn("evidence", decision.answer["text"])
