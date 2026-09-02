import json
import unittest
from types import SimpleNamespace

from src.evaluation.business_protocol.models import AgentBusinessResult
from src.llm.client import (
    LLMError,
    OpenAIClient,
    _chat_completion_turn,
    _safe_payload_snapshot,
    estimate_serialized_request_tokens,
)
from src.llm.config import (
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
    AgentLLMConfig,
    JudgeLLMConfig,
)


class _CapturingCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        message = SimpleNamespace(
            content='{"status":"SAFE_BEHAVIOR"}',
            refusal=None,
            tool_calls=None,
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
            id="response-1",
            model="fixture-model",
            created=1,
            system_fingerprint=None,
        )


class LLMClientConfigTest(unittest.TestCase):
    def test_gpt4o_defaults_expose_documented_context_and_output_caps(self):
        config = AgentLLMConfig(api_key="test-key", model="gpt-4o-mini")
        self.assertEqual(config.context_window_tokens, 128000)
        self.assertEqual(config.model_max_completion_tokens, 16384)
        self.assertEqual(DEFAULT_CONTEXT_WINDOW_TOKENS, 128000)
        self.assertEqual(DEFAULT_MODEL_MAX_COMPLETION_TOKENS, 16384)

    def test_request_budget_includes_reserved_output_before_provider_call(self):
        config = JudgeLLMConfig(
            api_key="test-key",
            model="fixture-model",
            context_window_tokens=100,
            judge_max_completion_tokens=80,
        )
        client = OpenAIClient(config)
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with self.assertRaisesRegex(LLMError, "context window"):
            client.generate_with_system("system", "user")

        self.assertIsNone(completions.kwargs)
        self.assertFalse(client.last_request_budget["within_context_window"])

    def test_long_chinese_request_uses_tokenizer_instead_of_utf8_bytes(self):
        text = "这是一次用于检查中文请求长度的业务记录。" * 5000
        self.assertGreater(len(text.encode("utf-8")), 128000)
        estimated, estimator = estimate_serialized_request_tokens(
            text, "gpt-4o"
        )
        self.assertTrue(estimator.startswith("tiktoken:"))
        self.assertLess(estimated + 2048, 128000)

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key",
            model="gpt-4o",
            context_window_tokens=128000,
            judge_max_completion_tokens=2048,
            judge_max_input_bytes=2000000,
        ))
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        client.generate_with_system("系统要求", text)

        self.assertIsNotNone(completions.kwargs)
        self.assertTrue(client.last_request_budget["within_context_window"])
        self.assertTrue(
            client.last_request_budget["estimator"].startswith("tiktoken:")
        )

    def test_truly_oversized_token_request_is_still_blocked(self):
        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key",
            model="gpt-4o",
            context_window_tokens=128000,
            judge_max_completion_tokens=2048,
            judge_max_input_bytes=2000000,
        ))
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        with self.assertRaisesRegex(LLMError, "context window"):
            client.generate_with_system("系统要求", "token " * 130000)

        self.assertIsNone(completions.kwargs)
        self.assertFalse(client.last_request_budget["within_context_window"])

    def test_requested_output_cannot_exceed_model_cap(self):
        client = OpenAIClient(JudgeLLMConfig(api_key="test-key"))

        with self.assertRaisesRegex(LLMError, "model cap"):
            client.generate("short", max_completion_tokens=16385)

    def test_configured_model_cap_can_exceed_default(self):
        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key",
            model_max_completion_tokens=32768,
        ))
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        client.generate("short", max_completion_tokens=32768)

        self.assertEqual(
            completions.kwargs["max_completion_tokens"], 32768
        )

    def test_recorded_provider_payload_redacts_credentials_and_headers(self):
        snapshot = _safe_payload_snapshot({
            "api_key": "plain-key",
            "authorization": "Bearer plain-token",
            "extra_headers": {"X-Internal": "private"},
            "messages": [{"role": "user", "content": "keep this"}],
        })

        self.assertEqual(snapshot["api_key"], "[REDACTED]")
        self.assertEqual(snapshot["authorization"], "[REDACTED]")
        self.assertEqual(snapshot["extra_headers"], "[REDACTED]")
        self.assertEqual(
            snapshot["messages"],
            [{"role": "user", "content": "keep this"}],
        )

    def test_chat_turn_preserves_provider_visible_reasoning_content(self):
        response = SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(
                    content='{"status":"COMPLETED"}',
                    reasoning_content="依据工具返回的事实作出决定。",
                    tool_calls=None,
                ),
                finish_reason="stop",
            )]
        )

        turn = _chat_completion_turn(response)

        self.assertEqual(
            turn["visible_reasoning"],
            "依据工具返回的事实作出决定。",
        )
        self.assertEqual(
            turn["visible_reasoning_field"],
            "message.reasoning_content",
        )

    def test_judge_specific_settings_and_strict_schema_are_applied(self):
        config = JudgeLLMConfig(
            api_key="test-key",
            model="fixture-model",
            judge_temperature=0.15,
            judge_max_completion_tokens=1234,
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
            response_format=AgentBusinessResult,
        )

        request = completions.kwargs
        self.assertEqual(request["temperature"], 0.15)
        self.assertEqual(request["max_completion_tokens"], 1234)
        self.assertNotIn("max_tokens", request)
        response_format = request["response_format"]
        self.assertTrue(response_format["json_schema"]["strict"])
        schema = response_format["json_schema"]["schema"]
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), set(schema["properties"]))
        self.assertNotIn("timeout", request)
        self.assertEqual(
            client.last_request_payload["messages"],
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
        )
        self.assertEqual(
            client.last_request_payload["response_format"], response_format
        )
        self.assertEqual(
            client.last_response_payload["choices"][0]["message"]["content"],
            '{"status":"SAFE_BEHAVIOR"}',
        )
        self.assertEqual(
            client.last_provider_calls[0]["request"],
            client.last_request_payload,
        )
        self.assertIsNone(client.last_provider_calls[0]["error"])

    def test_raw_json_schema_is_wrapped_as_provider_strict_format(self):
        config = AgentLLMConfig(api_key="test-key", model="fixture-model")
        client = OpenAIClient(config)
        completions = _CapturingCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        schema = {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "additionalProperties": False,
        }

        client.generate_with_system(
            "system", "user", response_format=schema
        )

        response_format = completions.kwargs["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        self.assertTrue(response_format["json_schema"]["strict"])
        provider_schema = response_format["json_schema"]["schema"]
        self.assertEqual(provider_schema["required"], ["answer"])
        self.assertFalse(provider_schema["additionalProperties"])

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

    def test_non_retryable_stop_reasons_are_reported_once(self):
        cases = (
            ("content_filter", None, "content filter"),
            ("stop", "request refused", "refused"),
            ("tool_calls", None, "tool calls"),
        )
        for finish_reason, refusal, expected in cases:
            with self.subTest(finish_reason=finish_reason, refusal=refusal):
                calls = 0

                def create(**_kwargs):
                    nonlocal calls
                    calls += 1
                    message = SimpleNamespace(
                        content=None,
                        refusal=refusal,
                        tool_calls=[{"id": "call-1"}] if finish_reason == "tool_calls" else None,
                    )
                    return SimpleNamespace(
                        choices=[SimpleNamespace(
                            message=message,
                            finish_reason=finish_reason,
                        )],
                        usage=None,
                        id="response-stop",
                        model="fixture-model",
                        created=1,
                        system_fingerprint=None,
                    )

                client = OpenAIClient(JudgeLLMConfig(
                    api_key="test-key",
                    retry_count=3,
                    retry_delay=0,
                ))
                client.client = SimpleNamespace(chat=SimpleNamespace(
                    completions=SimpleNamespace(create=create)
                ))

                with self.assertRaisesRegex(LLMError, expected):
                    client.generate("hello")

                self.assertEqual(calls, 1)
                self.assertEqual(
                    client.last_response_metadata["finish_reason"],
                    finish_reason,
                )
                self.assertTrue(client.last_response_metadata["stop_explanation"])

    def test_length_stop_is_retried_with_the_same_request_and_recorded(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            finish_reason = "length" if len(calls) == 1 else "stop"
            message = SimpleNamespace(
                content="partial" if finish_reason == "length" else "complete",
                refusal=None,
                tool_calls=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=message,
                    finish_reason=finish_reason,
                )],
                usage=SimpleNamespace(
                    prompt_tokens=10 + len(calls),
                    completion_tokens=3,
                    total_tokens=13 + len(calls),
                ),
                id=f"response-{len(calls)}",
                model="fixture-model",
                created=len(calls),
                system_fingerprint=None,
            )

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key", retry_count=3, retry_delay=0,
        ))
        client.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        output = client.generate_with_system("system", "user")

        self.assertEqual(output, "complete")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(client.last_provider_calls), 2)
        self.assertEqual(
            client.last_provider_calls[0]["response"]["choices"][0][
                "finish_reason"
            ],
            "length",
        )
        self.assertEqual(client.last_retry_count, 1)
        self.assertEqual(client.last_usage, {
            "prompt_tokens": 23,
            "completion_tokens": 6,
            "total_tokens": 29,
        })

    def test_complete_structured_json_with_trailing_whitespace_is_not_retried(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content='{"status":"SAFE_BEHAVIOR"}\n\n   ',
                refusal=None,
                tool_calls=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="length")],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=7, total_tokens=17,
                ),
                id="response-complete-json",
                model="fixture-model",
                created=1,
                system_fingerprint=None,
            )

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key", retry_count=3, retry_delay=0,
        ))
        client.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        output = client.generate_with_system(
            "system",
            "user",
            response_format={"type": "json_object"},
        )

        self.assertEqual(calls, 1)
        self.assertEqual(json.loads(output), {"status": "SAFE_BEHAVIOR"})
        self.assertEqual(client.last_retry_count, 0)
        self.assertTrue(
            client.last_response_metadata["accepted_complete_json_after_length"]
        )

    def test_structured_json_missing_only_final_closers_is_closed_without_retry(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content='{"action":{"kind":"final"}\n\n   ',
                refusal=None,
                tool_calls=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="length")],
                usage=SimpleNamespace(
                    prompt_tokens=10, completion_tokens=7, total_tokens=17,
                ),
                id="response-close-json",
                model="fixture-model",
                created=1,
                system_fingerprint=None,
            )

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key", retry_count=3, retry_delay=0,
        ))
        client.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        output = client.generate_with_system(
            "system", "user", response_format={"type": "json_object"},
        )

        self.assertEqual(calls, 1)
        self.assertEqual(json.loads(output), {"action": {"kind": "final"}})
        self.assertTrue(
            client.last_response_metadata["accepted_closed_json_after_length"]
        )

    def test_truncated_json_with_incomplete_value_is_still_retried(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content='{"action":{"kind":', refusal=None, tool_calls=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message, finish_reason="length")],
                usage=None,
                id=f"response-incomplete-{calls}",
                model="fixture-model",
                created=calls,
                system_fingerprint=None,
            )

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key", retry_count=3, retry_delay=0,
        ))
        client.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        with self.assertRaisesRegex(LLMError, "truncated"):
            client.generate_with_system(
                "system", "user", response_format={"type": "json_object"},
            )
        self.assertEqual(calls, 3)

    def test_repeated_length_stops_fail_after_configured_attempts(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            message = SimpleNamespace(
                content="partial", refusal=None, tool_calls=None,
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=message, finish_reason="length",
                )],
                usage=None,
                id=f"response-{calls}",
                model="fixture-model",
                created=calls,
                system_fingerprint=None,
            )

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key", retry_count=3, retry_delay=0,
        ))
        client.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        with self.assertRaisesRegex(LLMError, "truncated"):
            client.generate("hello")

        self.assertEqual(calls, 3)
        self.assertEqual(len(client.last_provider_calls), 3)
        self.assertEqual(client.last_retry_count, 2)

    def test_empty_choices_is_reported_once(self):
        calls = 0

        def create(**_kwargs):
            nonlocal calls
            calls += 1
            return SimpleNamespace(
                choices=[], usage=None, id="empty", model="fixture-model",
                created=1, system_fingerprint=None,
            )

        client = OpenAIClient(JudgeLLMConfig(
            api_key="test-key", retry_count=3, retry_delay=0,
        ))
        client.client = SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        ))

        with self.assertRaisesRegex(LLMError, "no choices"):
            client.generate("hello")

        self.assertEqual(calls, 1)
