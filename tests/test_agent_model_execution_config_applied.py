import unittest
from unittest.mock import patch

from scripts.run_agent_model_suite import build_environment
from src.agents.ioa_agent import _with_agent_model_response_format
from src.evaluation.agent_model.models import AgentModelAction
from src.runtime.base import AgentInvocation
from src.runtime.llm_runtime import LLMAgentRuntime


class _CapturingClient:
    model = "fixture-model"
    temperature = 0.9

    def __init__(self):
        self.kwargs = None

    def generate_with_system(self, system, prompt, **kwargs):
        self.kwargs = kwargs
        return '{"type":"final","business_output":{"answer":"ok"},"behavior_record":{}}'


class AgentModelExecutionConfigAppliedTest(unittest.IsolatedAsyncioTestCase):
    def test_controlled_ag2_config_uses_provider_enforced_schema(self):
        original = {"config_list": [{"model": "fixture-model"}]}

        configured = _with_agent_model_response_format(original, True)

        self.assertNotIn("response_format", original["config_list"][0])
        self.assertIs(
            configured["config_list"][0]["response_format"], AgentModelAction
        )

    async def test_live_suite_enables_structured_agent_output_only_in_its_environment(self):
        class FakeEnvironment:
            def __init__(self, config):
                self.config = config

            def add_sub_ioa(self, _sub_ioa_id):
                pass

            async def setup_default_agents(self):
                pass

            async def setup_default_topology(self, _topology):
                pass

        with patch(
            "scripts.run_agent_model_suite.IoAEnvironment", FakeEnvironment
        ):
            environment = await build_environment(False)

        self.assertTrue(environment.config["agent_model_structured_output"])

    async def test_runtime_applies_case_request_parameters(self):
        client = _CapturingClient()
        runtime = LLMAgentRuntime("agent-1", client)
        invocation = AgentInvocation(
            task_id="task-1",
            trace_id="trace-1",
            agent_id="agent-1",
            input={"task": "controlled task"},
            metadata={
                "model_request_config": {
                    "temperature": 0.2,
                    "top_p": 0.8,
                    "max_tokens": 777,
                    "timeout": 55,
                    "retry_count": 4,
                    "retry_delay": 0.25,
                },
            },
        )

        result = await runtime.invoke(invocation)

        self.assertEqual(client.kwargs, {
            "temperature": 0.2,
            "top_p": 0.8,
            "max_tokens": 777,
            "timeout": 55,
            "retry_count": 4,
            "retry_delay": 0.25,
        })
        self.assertEqual(
            result.metadata["applied_model_request_config"]["max_tokens"], 777
        )
        trace = result.metadata["model_call_trace"]
        self.assertEqual(trace["agent_id"], "agent-1")
        self.assertEqual(trace["model"], "fixture-model")
        self.assertEqual(trace["request"]["config"]["top_p"], 0.8)
        self.assertIn("controlled task", trace["request"]["messages"][1]["content"])
        self.assertIn("business_output", trace["response"]["raw"])
