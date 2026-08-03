"""LLM client for IOA evaluation environment.

Provides OpenAI-compatible client with retry logic.
Used by agents, judges, and attack generators.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from typing import Any, Optional

from .config import (
    AgentLLMConfig,
    DEFAULT_CONTEXT_WINDOW_TOKENS,
    DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
    JudgeLLMConfig,
    get_agent_llm_config,
    get_judge_llm_config,
)

logger = logging.getLogger(__name__)


_SENSITIVE_PAYLOAD_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "password",
    "secret",
    "access_token",
    "refresh_token",
    "cookie",
    "set-cookie",
    "headers",
    "extra_headers",
    "default_headers",
    "http_headers",
}


def estimate_serialized_request_tokens(
    serialized: str,
    model: str,
) -> tuple[int, str]:
    """Estimate request tokens with the tokenizer used by GPT-4o models.

    The previous byte-count fallback treated every UTF-8 byte as one token.
    That substantially over-counted Chinese requests and rejected valid
    requests before they reached the provider.
    """
    try:
        import tiktoken

        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("o200k_base")
        return len(encoding.encode(serialized)) + 64, (
            f"tiktoken:{encoding.name}"
        )
    except ImportError:
        # Kept only as a fail-safe for incomplete installations.  tiktoken is
        # an explicit project dependency, so normal runs use the branch above.
        return len(serialized.encode("utf-8")) + 64, "utf8_bytes_upper_bound"


def _safe_payload_snapshot(value: Any) -> Any:
    """Return a JSON-safe snapshot without credentials or live objects."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        try:
            return _safe_payload_snapshot(value.model_dump(mode="json"))
        except TypeError:
            return _safe_payload_snapshot(value.model_dump())
    if isinstance(value, Mapping):
        snapshot: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key)
            if normalized_key.lower() in _SENSITIVE_PAYLOAD_KEYS:
                snapshot[normalized_key] = "[REDACTED]"
            else:
                snapshot[normalized_key] = _safe_payload_snapshot(item)
        return snapshot
    if isinstance(value, (list, tuple, set)):
        return [_safe_payload_snapshot(item) for item in value]
    if hasattr(value, "__dict__"):
        return _safe_payload_snapshot({
            key: item
            for key, item in vars(value).items()
            if not str(key).startswith("_")
        })
    return str(value)


class LLMError(Exception):
    pass


class LLMResponseError(LLMError):
    """A completed provider response that must not be retried unchanged."""


class LLMTruncatedResponseError(LLMResponseError):
    """A response cut off at the configured output limit."""


class BaseLLMClient(ABC):
    @abstractmethod
    def generate(self, prompt: str, **kwargs) -> str:
        pass

    @abstractmethod
    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        pass


class OpenAIClient(BaseLLMClient):
    """OpenAI-compatible client with retry."""

    def __init__(self, config=None):
        try:
            import openai
        except ImportError:
            raise LLMError("openai not installed. pip install openai")

        self.config = config
        self.retry_count = getattr(config, "retry_count", 1) if config else 1
        self.retry_delay = getattr(config, "retry_delay", 1.0) if config else 1.0
        self.timeout = getattr(config, "timeout", None) if config else None

        client_kwargs = {"api_key": config.get_api_key()}
        if config.base_url:
            client_kwargs["base_url"] = config.base_url
        if self.timeout:
            client_kwargs["timeout"] = self.timeout

        self.client = openai.OpenAI(**client_kwargs)
        self.model = config.model
        if isinstance(config, JudgeLLMConfig):
            self.temperature = config.judge_temperature
            self.max_completion_tokens = config.judge_max_completion_tokens
            self.max_input_bytes = config.judge_max_input_bytes
        else:
            self.temperature = config.temperature
            self.max_completion_tokens = config.max_completion_tokens
            self.max_input_bytes = None
        self.context_window_tokens = min(
            int(getattr(config, "context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)),
            DEFAULT_CONTEXT_WINDOW_TOKENS,
        )
        self.model_max_completion_tokens = min(
            int(getattr(
                config,
                "model_max_completion_tokens",
                DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
            )),
            DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
        )
        if self.context_window_tokens <= 0:
            raise LLMError("context_window_tokens must be positive")
        if self.model_max_completion_tokens <= 0:
            raise LLMError("model_max_completion_tokens must be positive")
        self.last_usage: dict[str, int] | None = None
        self.last_retry_count = 0
        self.last_latency_ms: float | None = None
        self.last_attempts: list[dict[str, object]] = []
        self.last_response_metadata: dict[str, object] = {}
        self.last_request_payload: dict[str, Any] = {}
        self.last_response_payload: Any = None
        self.last_provider_calls: list[dict[str, Any]] = []
        self.last_request_budget: dict[str, Any] = {}

    def _reset_provider_records(self) -> None:
        self.last_usage = None
        self.last_retry_count = 0
        self.last_latency_ms = None
        self.last_response_metadata = {}
        self.last_request_payload = {}
        self.last_response_payload = None
        self.last_provider_calls = []
        self.last_request_budget = {}

    def _create_chat_completion(self, request_kwargs: dict[str, Any]):
        request_snapshot = _safe_payload_snapshot(request_kwargs)
        record: dict[str, Any] = {
            "attempt": len(self.last_provider_calls) + 1,
            "request": request_snapshot,
            "response": None,
            "error": None,
            "latency_ms": None,
            "request_budget": _safe_payload_snapshot(self.last_request_budget),
        }
        self.last_request_payload = request_snapshot
        self.last_provider_calls.append(record)
        started = time.perf_counter()
        try:
            response = self.client.chat.completions.create(**request_kwargs)
        except Exception as exc:
            error = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            record["error"] = error
            self.last_response_payload = {"error": error}
            raise
        else:
            response_snapshot = _safe_payload_snapshot(response)
            record["response"] = response_snapshot
            self.last_response_payload = response_snapshot
            self.last_usage = _merge_token_usage(
                self.last_usage, self._usage(response)
            )
            self.last_response_metadata = self._response_metadata(response)
            return response
        finally:
            record["latency_ms"] = (time.perf_counter() - started) * 1000

    def _with_retry(
        self,
        fn,
        *,
        retry_count: int | None = None,
        retry_delay: float | None = None,
    ) -> str:
        last_err = None
        started = time.perf_counter()
        self.last_attempts = []
        effective_retry_count = max(1, int(retry_count or self.retry_count))
        effective_retry_delay = float(
            self.retry_delay if retry_delay is None else retry_delay
        )
        for attempt in range(effective_retry_count):
            attempt_started = time.perf_counter()
            try:
                result = fn()
                self.last_attempts.append({
                    "attempt": attempt + 1,
                    "status": "completed",
                    "latency_ms": (time.perf_counter() - attempt_started) * 1000,
                    "error": None,
                })
                self.last_retry_count = attempt
                self.last_latency_ms = (time.perf_counter() - started) * 1000
                return result
            except Exception as e:
                last_err = e
                self.last_attempts.append({
                    "attempt": attempt + 1,
                    "status": "failed",
                    "latency_ms": (time.perf_counter() - attempt_started) * 1000,
                    "error": str(e),
                })
                if isinstance(e, LLMResponseError) and not isinstance(
                    e, LLMTruncatedResponseError
                ):
                    self.last_retry_count = attempt
                    self.last_latency_ms = (time.perf_counter() - started) * 1000
                    raise
                if attempt < effective_retry_count - 1:
                    time.sleep(effective_retry_delay)
        self.last_retry_count = max(0, effective_retry_count - 1)
        self.last_latency_ms = (time.perf_counter() - started) * 1000
        if isinstance(last_err, LLMResponseError):
            raise last_err
        raise LLMError(f"API error after {effective_retry_count} attempts: {last_err}")

    def generate(self, prompt: str, **kwargs) -> str:
        self._reset_provider_records()

        messages = [{"role": "user", "content": prompt}]
        max_completion_tokens = self._requested_completion_tokens(kwargs)
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", 1.0),
            "max_completion_tokens": max_completion_tokens,
        }
        self._check_request_budget(request_kwargs)
        if kwargs.get("timeout") is not None:
            request_kwargs["timeout"] = kwargs["timeout"]

        def _do():
            resp = self._create_chat_completion(request_kwargs)
            return _checked_response_text(resp)
        return self._with_retry(
            _do,
            retry_count=kwargs.get("retry_count"),
            retry_delay=kwargs.get("retry_delay"),
        )

    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        self._reset_provider_records()
        if self.max_input_bytes is not None:
            request_bytes = len(system.encode("utf-8")) + len(user.encode("utf-8"))
            if request_bytes > self.max_input_bytes:
                raise LLMError(
                    "LLM request exceeds configured input budget: "
                    f"{request_bytes} > {self.max_input_bytes} bytes"
                )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        response_format = kwargs.get("response_format")
        json_validator = _response_format_validator(response_format)
        request_kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": kwargs.get("temperature", self.temperature),
            "top_p": kwargs.get("top_p", 1.0),
            "max_completion_tokens": self._requested_completion_tokens(kwargs),
        }
        if response_format is not None:
            request_kwargs["response_format"] = _response_format_payload(
                response_format
            )
        self._check_request_budget(request_kwargs)
        if kwargs.get("timeout") is not None:
            request_kwargs["timeout"] = kwargs["timeout"]

        def _do():
            resp = self._create_chat_completion(request_kwargs)
            text = _checked_response_text(
                resp,
                accept_complete_json_on_length=response_format is not None,
                json_validator=json_validator,
            )
            if response_format is not None:
                recovered = _json_completion_after_length(
                    resp, json_validator=json_validator
                )
                if recovered is not None:
                    metadata_key = (
                        "accepted_complete_json_after_length"
                        if recovered[0] == "complete"
                        else "accepted_closed_json_after_length"
                    )
                    self.last_response_metadata[metadata_key] = True
            return text
        return self._with_retry(
            _do,
            retry_count=kwargs.get("retry_count"),
            retry_delay=kwargs.get("retry_delay"),
        )

    def _requested_completion_tokens(self, kwargs: dict[str, Any]) -> int:
        requested = kwargs.get("max_completion_tokens", self.max_completion_tokens)
        try:
            value = int(requested)
        except (TypeError, ValueError) as exc:
            raise LLMError("max_completion_tokens must be an integer") from exc
        if value <= 0:
            raise LLMError("max_completion_tokens must be positive")
        if value > self.model_max_completion_tokens:
            raise LLMError(
                "max_completion_tokens exceeds the model cap: "
                f"{value} > {self.model_max_completion_tokens}"
            )
        return value

    def _check_request_budget(self, request_kwargs: dict[str, Any]) -> None:
        """Fail before the provider call when input plus output is too large."""
        messages = request_kwargs.get("messages", [])
        serialized = json.dumps(
            {"messages": messages, "response_format": request_kwargs.get("response_format")},
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        )
        estimated_input_tokens, estimator = estimate_serialized_request_tokens(
            serialized,
            str(request_kwargs.get("model", self.model) or self.model),
        )
        reserved_output_tokens = int(request_kwargs["max_completion_tokens"])
        total_reserved = estimated_input_tokens + reserved_output_tokens
        self.last_request_budget = {
            "estimator": estimator,
            "estimated_input_tokens": estimated_input_tokens,
            "reserved_output_tokens": reserved_output_tokens,
            "context_window_tokens": self.context_window_tokens,
            "total_reserved_tokens": total_reserved,
            "within_context_window": total_reserved <= self.context_window_tokens,
        }
        if total_reserved > self.context_window_tokens:
            raise LLMError(
                "LLM request exceeds model context window: "
                f"estimated input {estimated_input_tokens} + reserved output "
                f"{reserved_output_tokens} > {self.context_window_tokens} tokens"
            )

    def generate_json(self, system: str, user: str, **kwargs) -> dict:
        """Generate and parse JSON response."""
        raw = self.generate_with_system(system, user, **kwargs)
        # Strip markdown code blocks if present
        raw = raw.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            json_lines = []
            in_json = False
            for line in lines:
                if line.startswith("```") and not in_json:
                    in_json = True
                    continue
                elif line.startswith("```") and in_json:
                    break
                elif in_json:
                    json_lines.append(line)
            raw = "\n".join(json_lines)
        return json.loads(raw)

    @staticmethod
    def _usage(response) -> dict[str, int] | None:
        usage = getattr(response, "usage", None)
        if usage is None:
            return None
        return {
            "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    @staticmethod
    def _response_metadata(response) -> dict[str, object]:
        choices = getattr(response, "choices", None) or []
        choice = choices[0] if choices else None
        message = getattr(choice, "message", None) if choice is not None else None
        finish_reason = getattr(choice, "finish_reason", None)
        return {
            "response_id": str(getattr(response, "id", "") or ""),
            "model": str(getattr(response, "model", "") or ""),
            "created": getattr(response, "created", None),
            "system_fingerprint": getattr(response, "system_fingerprint", None),
            "finish_reason": finish_reason,
            "stop_explanation": _finish_reason_explanation(finish_reason),
            "refusal": getattr(message, "refusal", None),
        }


def get_agent_llm_client() -> OpenAIClient:
    return OpenAIClient(get_agent_llm_config())


def get_judge_llm_client() -> OpenAIClient:
    return OpenAIClient(get_judge_llm_config())


def _response_format_payload(response_format):
    """Convert a Pydantic model into provider-enforced strict JSON Schema."""
    if isinstance(response_format, dict):
        if response_format.get("type") in {"json_object", "json_schema"}:
            return response_format
        schema = _make_strict_json_schema(response_format)
        return {
            "type": "json_schema",
            "json_schema": {
                "name": "agent_model_action",
                "strict": True,
                "schema": schema,
            },
        }
    if not hasattr(response_format, "model_json_schema"):
        raise TypeError("response_format must be a mapping or Pydantic model")
    schema = _make_strict_json_schema(response_format.model_json_schema())
    return {
        "type": "json_schema",
        "json_schema": {
            "name": response_format.__name__,
            "strict": True,
            "schema": schema,
        },
    }


def _make_strict_json_schema(value):
    if isinstance(value, list):
        return [_make_strict_json_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    strict = {
        key: _make_strict_json_schema(item)
        for key, item in value.items()
    }
    properties = strict.get("properties")
    if isinstance(properties, dict):
        strict["additionalProperties"] = False
        strict["required"] = list(properties)
    return strict


def _response_format_validator(
    response_format: Any,
) -> Callable[[dict[str, Any]], Any] | None:
    """Return the model validator associated with a structured response."""
    model_validate = getattr(response_format, "model_validate", None)
    return model_validate if callable(model_validate) else None


def _checked_response_text(
    response,
    *,
    allow_tool_calls: bool = False,
    accept_complete_json_on_length: bool = False,
    json_validator: Callable[[dict[str, Any]], Any] | None = None,
) -> str:
    """Return response text or explain why the provider did not finish normally."""
    choices = getattr(response, "choices", None) or []
    if not choices:
        raise LLMResponseError("Model response contained no choices")
    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise LLMResponseError("Model response contained no message")

    refusal = getattr(message, "refusal", None)
    if refusal:
        raise LLMResponseError(f"Model refused the request: {refusal}")

    finish_reason = getattr(choice, "finish_reason", None)
    if finish_reason == "length":
        if accept_complete_json_on_length:
            recovered = _json_completion_after_length(
                response, json_validator=json_validator
            )
            if recovered is not None:
                return recovered[1]
        raise LLMTruncatedResponseError(
            "Model response was truncated because the completion token limit was "
            "reached (finish_reason=length)"
        )
    if finish_reason == "content_filter":
        raise LLMResponseError(
            "Model response was stopped by the content filter "
            "(finish_reason=content_filter)"
        )
    if finish_reason in {"tool_calls", "function_call"} and not allow_tool_calls:
        raise LLMResponseError(
            "Model stopped to request tool calls, but this text-only request does not "
            f"execute them (finish_reason={finish_reason})"
        )
    if finish_reason not in {None, "stop", "tool_calls", "function_call"}:
        raise LLMResponseError(
            f"Model response stopped for an unsupported reason: {finish_reason}"
        )
    return getattr(message, "content", None) or ""


def _finish_reason_explanation(finish_reason: Any) -> str:
    explanations = {
        "stop": "模型正常结束回答。",
        "length": "模型达到预留的最大输出长度后停止。",
        "content_filter": "模型因提供方内容过滤而停止。",
        "tool_calls": "模型停止文本输出并请求工具调用。",
        "function_call": "模型停止文本输出并请求函数调用。",
    }
    if finish_reason in explanations:
        return explanations[finish_reason]
    if finish_reason is None:
        return "提供方没有返回停止原因。"
    return f"模型因提供方返回的停止原因 {finish_reason!r} 停止。"


def _is_complete_json_after_length(response: Any) -> bool:
    """Return true only when a length-stopped response is a complete JSON object."""
    recovered = _json_completion_after_length(response)
    return recovered is not None and recovered[0] == "complete"


def _json_completion_after_length(
    response: Any,
    *,
    json_validator: Callable[[dict[str, Any]], Any] | None = None,
) -> tuple[str, str] | None:
    """Return a usable JSON object and how it was obtained after a length stop.

    Providers occasionally emit every required field, omit only the final
    closing container delimiters, and then spend the remaining token budget on
    whitespace.  In that narrow case, close the still-open JSON containers.
    No strings, values, commas, or keys are invented.  The untouched provider
    response remains in the provider-call audit record.
    """
    choices = getattr(response, "choices", None) or []
    if not choices or getattr(choices[0], "finish_reason", None) != "length":
        return None
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if not isinstance(content, str) or not content.strip():
        return None
    stripped = content.rstrip()
    try:
        parsed = json.loads(stripped)
    except json.JSONDecodeError:
        repaired = _close_unfinished_json_containers(stripped)
        if repaired is None:
            return None
        try:
            parsed = json.loads(repaired)
        except json.JSONDecodeError:
            return None
        if isinstance(parsed, dict) and _passes_json_validator(
            parsed, json_validator
        ):
            return "closed_containers", repaired
        return None
    if isinstance(parsed, dict) and _passes_json_validator(
        parsed, json_validator
    ):
        return "complete", content
    return None


def _passes_json_validator(
    parsed: dict[str, Any],
    validator: Callable[[dict[str, Any]], Any] | None,
) -> bool:
    if validator is None:
        return True
    try:
        validator(parsed)
    except Exception:
        return False
    return True


def _close_unfinished_json_containers(content: str) -> str | None:
    """Close unmatched JSON objects/arrays when that alone makes valid JSON."""
    stack: list[str] = []
    in_string = False
    escaped = False
    pairs = {"}": "{", "]": "["}
    for character in content:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "{[":
            stack.append(character)
        elif character in "}]":
            if not stack or stack[-1] != pairs[character]:
                return None
            stack.pop()
    if in_string or escaped or not stack:
        return None
    suffix = "".join("}" if opener == "{" else "]" for opener in reversed(stack))
    candidate = content + suffix
    try:
        json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return candidate


def _merge_token_usage(
    accumulated: dict[str, int] | None,
    current: dict[str, int] | None,
) -> dict[str, int] | None:
    """Add token usage from every real provider request in one logical call."""
    if current is None:
        return accumulated
    total = dict(accumulated or {})
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[key] = int(total.get(key, 0) or 0) + int(current.get(key, 0) or 0)
    return total
