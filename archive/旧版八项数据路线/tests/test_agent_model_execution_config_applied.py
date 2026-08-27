import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from scripts.run_agent_model_suite import build_environment
from src.agents.ioa_agent import (
    IoAAgent,
    _checked_ag2_client,
    _with_agent_model_response_format,
)
from src.evaluation.agent_model.models import AgentModelAction
from src.runtime.ag2_runtime import AG2AgentRuntime
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
                    "max_completion_tokens": 777,
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
            "max_completion_tokens": 777,
            "timeout": 55,
            "retry_count": 4,
            "retry_delay": 0.25,
        })
        self.assertEqual(
            result.metadata["applied_model_request_config"]["max_completion_tokens"], 777
        )
        trace = result.metadata["model_call_trace"]
        self.assertEqual(trace["agent_id"], "agent-1")
        self.assertEqual(trace["model"], "fixture-model")
        self.assertEqual(trace["request"]["config"]["top_p"], 0.8)
        self.assertIn("controlled task", trace["request"]["messages"][1]["content"])
        self.assertIn("business_output", trace["response"]["raw"])

    async def test_ag2_runtime_applies_and_records_case_request_parameters(self):
        class FakeIoAAgent:
            structured_output_schema = "AgentModelAction"
            model = "fixture-model"
            last_usage = None
            last_retry_count = 0

            def __init__(self):
                self.request_config = None

            def run_task(self, task, max_turns=1, model_request_config=None):
                self.request_config = model_request_config
                return '{"type":"final","business_output":{"answer":"ok"},"behavior_record":{},"tool_call":null}'

        ioa_agent = FakeIoAAgent()
        runtime = AG2AgentRuntime("agent-1", {}, ioa_agent)
        expected = {
            "temperature": 0.2,
            "top_p": 0.8,
            "max_completion_tokens": 777,
            "timeout": 55,
            "retry_count": 4,
            "retry_delay": 0.25,
        }
        invocation = AgentInvocation(
            task_id="task-1",
            trace_id="trace-1",
            agent_id="agent-1",
            input={"task": "controlled task"},
            metadata={"model_request_config": expected, "agentic_loop": True},
        )

        result = await runtime.invoke(invocation)

        self.assertEqual(ioa_agent.request_config, expected)
        self.assertEqual(result.metadata["applied_model_request_config"], expected)
        self.assertEqual(
            result.metadata["model_call_trace"]["request"]["config"], expected
        )

    async def test_ag2_runtime_passes_step_schema_without_polluting_pairing_config(self):
        class FakeIoAAgent:
            structured_output_schema = "AgentModelAction"
            model = "fixture-model"
            last_usage = None
            last_retry_count = 0

            def __init__(self):
                self.request_config = None

            def run_task(self, task, max_turns=1, model_request_config=None):
                self.request_config = model_request_config
                self.last_provider_calls = [{
                    "request": {
                        "model": "fixture-model",
                        "messages": [
                            {"role": "system", "content": "exact system"},
                            {"role": "user", "content": task},
                        ],
                        "response_format": {
                            "type": "json_schema",
                            "json_schema": {"schema": schema},
                        },
                    },
                    "response": {
                        "id": "response-1",
                        "choices": [{"message": {"content": "ok"}}],
                    },
                    "error": None,
                }]
                return '{"type":"final","business_output":{"answer":"ok"},"behavior_record":{},"tool_call":null}'

        schema = {
            "type": "object",
            "properties": {"type": {"type": "string", "const": "final"}},
            "required": ["type"],
            "additionalProperties": False,
        }
        expected = {"temperature": 0.2, "max_completion_tokens": 777}
        ioa_agent = FakeIoAAgent()
        runtime = AG2AgentRuntime("agent-1", {}, ioa_agent)
        result = await runtime.invoke(AgentInvocation(
            task_id="internal-task",
            trace_id="internal-trace",
            requester_id="internal-gateway",
            agent_id="agent-1",
            input={
                "task": "controlled task",
                "payload": {"controlled_agent_model_evaluation_step": True},
            },
            metadata={
                "model_request_config": expected,
                "visible_action_schema": schema,
                "agentic_loop": True,
            },
        ))

        self.assertEqual(ioa_agent.request_config["response_format"], schema)
        self.assertEqual(
            {key: value for key, value in ioa_agent.request_config.items()
             if key != "response_format"},
            expected,
        )
        trace_request = result.metadata["model_call_trace"]["request"]
        self.assertEqual(trace_request["config"], expected)
        self.assertTrue(trace_request["response_schema_hash"])
        self.assertEqual(trace_request["response_schema"], schema)
        self.assertEqual(trace_request["messages"][0]["role"], "system")
        self.assertEqual(
            trace_request["provider_payload"]["response_format"]
            ["json_schema"]["schema"],
            schema,
        )
        self.assertEqual(
            result.metadata["model_call_trace"]["response"]
            ["provider_payload"]["id"],
            "response-1",
        )

    def test_controlled_runtime_prompts_omit_internal_routing_ids(self):
        invocation = AgentInvocation(
            task_id="internal-task",
            trace_id="internal-trace",
            requester_id="internal-gateway",
            agent_id="agent-1",
            input={
                "task": "controlled task",
                "payload": {"controlled_agent_model_evaluation_step": True},
            },
            metadata={"structured_output_enforced": True},
        )
        prompts = [
            AG2AgentRuntime._build_prompt(invocation),
            LLMAgentRuntime("agent-1", client=object())._build_prompt(invocation),
        ]

        for prompt in prompts:
            self.assertIn("controlled task", prompt)
            for hidden in (
                "internal-task", "internal-trace", "internal-gateway",
                "Task ID", "Trace ID", "Requester",
                "任务ID", "请求方",
            ):
                self.assertNotIn(hidden, prompt)

    def test_ioa_agent_reports_requested_model_not_observed_version(self):
        ioa_agent = IoAAgent(
            sub_ioa_id="finance",
            agent=SimpleNamespace(name="fixture-agent"),
            user_proxy=SimpleNamespace(),
            config={},
            llm_config={"config_list": [{"model": "gpt-4o-mini"}]},
            last_response_metadata={"model": "gpt-4o-mini-2024-07-18"},
        )

        self.assertEqual(ioa_agent.model, "gpt-4o-mini")

    def test_ag2_client_retries_length_and_accumulates_usage(self):
        requests = []
        calls = 0

        def create(**kwargs):
            nonlocal calls
            calls += 1
            requests.append(kwargs)
            finish_reason = "length" if calls == 1 else "stop"
            message = SimpleNamespace(
                content="partial" if finish_reason == "length" else "complete",
                refusal=None,
                tool_calls=None,
                function_call=None,
            )
            return SimpleNamespace(
                id="response-1",
                choices=[SimpleNamespace(
                    message=message,
                    finish_reason=finish_reason,
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10 + calls,
                    completion_tokens=5,
                    total_tokens=15 + calls,
                ),
                model="fixture-model",
                created=1,
                system_fingerprint=None,
                cost=0,
            )

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "fixture-model",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {
                "temperature": 0.2,
                "top_p": 0.8,
                "max_completion_tokens": 777,
                "timeout": 55,
                "retry_count": 3,
                "retry_delay": 0,
            },
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )

        response = client.create(messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(response.choices[0].message.content, "complete")
        self.assertEqual(calls, 2)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(requests[0]["max_completion_tokens"], 777)
        self.assertNotIn("max_tokens", requests[0])
        provider_call = client.provider_call_records[0]
        self.assertEqual(
            provider_call["request_budget"]["context_window_tokens"], 128000
        )
        self.assertEqual(
            provider_call["request_budget"]["model_max_completion_tokens"],
            16384,
        )
        self.assertEqual(
            provider_call["request_budget"]["reserved_output_tokens"], 777
        )
        self.assertTrue(provider_call["request_budget"]["within_context_window"])
        self.assertEqual(
            provider_call["request"]["messages"],
            [{"role": "user", "content": "hello"}],
        )
        self.assertEqual(
            provider_call["response"]["choices"][0]["finish_reason"],
            "length",
        )
        self.assertEqual(len(client.provider_call_records), 2)
        self.assertEqual(client.last_retry_count, 1)
        self.assertEqual(client.last_usage, {
            "prompt_tokens": 23,
            "completion_tokens": 10,
            "total_tokens": 33,
        })

    def test_ag2_client_rejects_output_above_gpt4o_cap_before_provider(self):
        provider_calls = 0

        def create(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("provider must not be called")

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "gpt-4o-mini",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {
                "max_completion_tokens": 16385,
                "retry_count": 3,
                "retry_delay": 0,
            },
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with self.assertRaisesRegex(Exception, "model cap"):
            client.create(messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(provider_calls, 0)
        self.assertEqual(len(client.provider_call_records), 1)
        self.assertEqual(
            client.provider_call_records[0]["request_budget"][
                "model_max_completion_tokens"
            ],
            16384,
        )

    def test_ag2_client_rejects_context_overflow_before_provider(self):
        provider_calls = 0

        def create(**_kwargs):
            nonlocal provider_calls
            provider_calls += 1
            raise AssertionError("provider must not be called")

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "gpt-4o",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {
                "max_completion_tokens": 16384,
                "retry_count": 3,
                "retry_delay": 0,
            },
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with self.assertRaisesRegex(Exception, "context window"):
            client.create(messages=[{
                "role": "user",
                "content": "token " * 115000,
            }])

        self.assertEqual(provider_calls, 0)
        budget = client.provider_call_records[0]["request_budget"]
        self.assertFalse(budget["within_context_window"])
        self.assertGreater(budget["total_reserved_tokens"], 128000)
        self.assertTrue(budget["estimator"].startswith("tiktoken:"))

    def test_ag2_client_converts_legacy_max_tokens_before_provider(self):
        requests = []

        def create(**kwargs):
            requests.append(kwargs)
            return SimpleNamespace(
                id="response-legacy-setting",
                choices=[SimpleNamespace(
                    message=SimpleNamespace(
                        content="complete",
                        refusal=None,
                        tool_calls=None,
                        function_call=None,
                    ),
                    finish_reason="stop",
                )],
                usage=None,
                model="fixture-model",
                created=1,
                system_fingerprint=None,
                cost=0,
            )

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "fixture-model",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                    "max_tokens": 777,
                }],
                "cache_seed": None,
            },
            {"retry_count": 1},
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        client.create(messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(requests[0]["max_completion_tokens"], 777)
        self.assertNotIn("max_tokens", requests[0])

    def test_ag2_client_accepts_complete_json_before_length_whitespace(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content='{"action":{"kind":"final"}}\n\n   ',
                refusal=None,
                tool_calls=None,
                function_call=None,
            )
            return SimpleNamespace(
                id="response-complete-json",
                choices=[SimpleNamespace(message=message, finish_reason="length")],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=7, total_tokens=17,
                ),
                model="fixture-model",
                created=1,
                system_fingerprint=None,
                cost=0,
            )

        schema = {
            "type": "object",
            "properties": {"action": {"type": "object"}},
        }
        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "fixture-model",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {
                "response_format": schema,
                "retry_count": 3,
                "retry_delay": 0,
            },
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        response = client.create(messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(calls, 1)
        self.assertEqual(response.choices[0].finish_reason, "length")
        self.assertEqual(client.last_retry_count, 0)
        self.assertTrue(
            client.last_response_metadata["accepted_complete_json_after_length"]
        )

    def test_ag2_client_closes_only_missing_json_container_delimiter(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content='{"action":{"kind":"final"}\n\n   ',
                refusal=None,
                tool_calls=None,
                function_call=None,
            )
            return SimpleNamespace(
                id="response-close-json",
                choices=[SimpleNamespace(message=message, finish_reason="length")],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=7, total_tokens=17,
                ),
                model="fixture-model",
                created=1,
                system_fingerprint=None,
                cost=0,
            )

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "fixture-model",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {
                "response_format": {"type": "object"},
                "retry_count": 3,
                "retry_delay": 0,
            },
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        response = client.create(messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(calls, 1)
        self.assertEqual(
            json.loads(response.choices[0].message.content),
            {"action": {"kind": "final"}},
        )
        self.assertTrue(
            client.last_response_metadata["accepted_closed_json_after_length"]
        )
        self.assertEqual(
            client.provider_call_records[0]["response"]["choices"][0]["message"][
                "content"
            ],
            '{"action":{"kind":"final"}\n\n   ',
        )

    def test_ag2_client_rejects_closed_json_missing_required_tool_id(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content=(
                    '{"action":{"kind":"tool_call","tool_call":'
                    '{"arguments":{},"reason":"check"}\n   '
                ),
                refusal=None,
                tool_calls=None,
                function_call=None,
            )
            return SimpleNamespace(
                id=f"response-invalid-tool-{calls}",
                choices=[SimpleNamespace(message=message, finish_reason="length")],
                usage=None,
                model="fixture-model",
                created=calls,
                system_fingerprint=None,
                cost=0,
            )

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "fixture-model",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {
                "response_format": {"type": "object"},
                "retry_count": 3,
                "retry_delay": 0,
            },
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

        with self.assertRaisesRegex(Exception, "truncated"):
            client.create(messages=[{"role": "user", "content": "hello"}])

        self.assertEqual(calls, 3)
        self.assertNotIn(
            "accepted_closed_json_after_length",
            client.last_response_metadata,
        )

    def test_ag2_client_records_provider_normalized_response_schema(self):
        schema = {
            "type": "object",
            "properties": {"type": {"type": "string", "const": "final"}},
            "required": ["type"],
            "additionalProperties": False,
        }

        def create(**_kwargs):
            message = SimpleNamespace(
                content='{"type":"final"}',
                refusal=None,
                tool_calls=None,
                function_call=None,
            )
            return SimpleNamespace(
                id="response-schema",
                choices=[SimpleNamespace(
                    message=message,
                    finish_reason="stop",
                )],
                usage=None,
                model="fixture-model",
                created=1,
                system_fingerprint=None,
                cost=0,
            )

        client = _checked_ag2_client(
            {
                "config_list": [{
                    "model": "fixture-model",
                    "api_key": "test-key",
                    "base_url": "https://example.invalid/v1",
                }],
                "cache_seed": None,
            },
            {"response_format": schema},
            allow_tool_calls=False,
        )
        client._clients[0]._oai_client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create)
            )
        )

        client.create(messages=[
            {"role": "system", "content": "exact system"},
            {"role": "user", "content": "exact user"},
        ])

        provider_call = client.provider_call_records[0]
        self.assertEqual(provider_call["capture_level"], "provider")
        self.assertEqual(
            [item["role"] for item in provider_call["request"]["messages"]],
            ["system", "user"],
        )
        response_format = provider_call["request"]["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertEqual(response_format["json_schema"]["schema"], schema)
        self.assertEqual(provider_call["response"]["id"], "response-schema")
