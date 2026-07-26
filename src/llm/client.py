"""LLM client for IOA evaluation environment.

Provides OpenAI-compatible client with retry logic.
Used by agents, judges, and attack generators.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

from .config import (
    AgentLLMConfig,
    JudgeLLMConfig,
    get_agent_llm_config,
    get_judge_llm_config,
)

logger = logging.getLogger(__name__)


class LLMError(Exception):
    pass


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
            self.max_tokens = config.judge_max_tokens
            self.max_input_bytes = config.judge_max_input_bytes
        else:
            self.temperature = config.temperature
            self.max_tokens = config.max_tokens
            self.max_input_bytes = None
        self.last_usage: dict[str, int] | None = None
        self.last_retry_count = 0
        self.last_latency_ms: float | None = None
        self.last_attempts: list[dict[str, object]] = []
        self.last_response_metadata: dict[str, object] = {}

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
                if attempt < effective_retry_count - 1:
                    time.sleep(effective_retry_delay)
        self.last_retry_count = effective_retry_count
        self.last_latency_ms = (time.perf_counter() - started) * 1000
        raise LLMError(f"API error after {effective_retry_count} attempts: {last_err}")

    def generate(self, prompt: str, **kwargs) -> str:
        def _do():
            request_kwargs = {
                "model": self.model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": kwargs.get("temperature", self.temperature),
                "top_p": kwargs.get("top_p", 1.0),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            }
            if kwargs.get("timeout") is not None:
                request_kwargs["timeout"] = kwargs["timeout"]
            resp = self.client.chat.completions.create(**request_kwargs)
            self.last_usage = self._usage(resp)
            self.last_response_metadata = self._response_metadata(resp)
            return resp.choices[0].message.content or ""
        return self._with_retry(
            _do,
            retry_count=kwargs.get("retry_count"),
            retry_delay=kwargs.get("retry_delay"),
        )

    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        if self.max_input_bytes is not None:
            request_bytes = len(system.encode("utf-8")) + len(user.encode("utf-8"))
            if request_bytes > self.max_input_bytes:
                raise LLMError(
                    "LLM request exceeds configured input budget: "
                    f"{request_bytes} > {self.max_input_bytes} bytes"
                )

        def _do():
            request_kwargs = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "temperature": kwargs.get("temperature", self.temperature),
                "top_p": kwargs.get("top_p", 1.0),
                "max_tokens": kwargs.get("max_tokens", self.max_tokens),
            }
            response_format = kwargs.get("response_format")
            if response_format is not None:
                request_kwargs["response_format"] = _response_format_payload(
                    response_format
                )
            if kwargs.get("timeout") is not None:
                request_kwargs["timeout"] = kwargs["timeout"]
            resp = self.client.chat.completions.create(
                **request_kwargs,
            )
            self.last_usage = self._usage(resp)
            self.last_response_metadata = self._response_metadata(resp)
            return resp.choices[0].message.content or ""
        return self._with_retry(
            _do,
            retry_count=kwargs.get("retry_count"),
            retry_delay=kwargs.get("retry_delay"),
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
        return {
            "response_id": str(getattr(response, "id", "") or ""),
            "model": str(getattr(response, "model", "") or ""),
            "created": getattr(response, "created", None),
            "system_fingerprint": getattr(response, "system_fingerprint", None),
        }


def get_agent_llm_client() -> OpenAIClient:
    return OpenAIClient(get_agent_llm_config())


def get_judge_llm_client() -> OpenAIClient:
    return OpenAIClient(get_judge_llm_config())


def _response_format_payload(response_format):
    """Convert a Pydantic model into provider-enforced strict JSON Schema."""
    if isinstance(response_format, dict):
        return response_format
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
