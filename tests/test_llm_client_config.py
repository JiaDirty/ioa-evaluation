import unittest
from types import SimpleNamespace

from src.evaluation.agent_model.judge import AgentModelJudgeVerdict
from src.llm.client import LLMError, OpenAIClient
from src.llm.config import JudgeLLMConfig


class _CapturingCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(content='{"status":"SAFE_BEHAVIOR"}')
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=None,
            id="response-1",
            model="fixture-model",
            created=1,
            system_fingerprint=None,
        )


class LLMClientConfigTest(unittest.TestCase):
    def test_judge_specific_settings_and_strict_schema_are_applied(self):
        config = JudgeLLMConfig(
            api_key="test-key",
            model="fixture-model",
            judge_temperature=0.15,
            judge_max_tokens=1234,
            judge_max_input_bytes=10000,
            timeout=77,
        )
        client = OpenAIClient(config)
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        client.generate_with_system(
            "system",
            "user",
            response_format=AgentModelJudgeVerdict,
        )

        request = completions.kwargs
        self.assertEqual(request["temperature"], 0.15)
        self.assertEqual(request["max_tokens"], 1234)
        response_format = request["response_format"]
        self.assertTrue(response_format["json_schema"]["strict"])
        schema = response_format["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertNotIn("timeout", request)

    def test_per_call_timeout_is_sent_to_provider(self):
        config = JudgeLLMConfig(
            api_key="test-key",
            model="fixture-model",
            timeout=77,
        )
        client = OpenAIClient(config)
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        client.generate_with_system("system", "user", timeout=33)

        self.assertEqual(completions.kwargs["timeout"], 33)

    def test_judge_input_budget_fails_before_api_call(self):
        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key",
            judge_max_input_bytes=10,
        ))

        with self.assertRaisesRegex(LLMError, "input budget"):
            client.generate_with_system("system", "request over budget")
