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
        self.temperature = config.temperature
        self.max_tokens = config.max_tokens
        self.last_usage: dict[str, int] | None = None
        self.last_retry_count = 0
        self.last_latency_ms: float | None = None

    def _with_retry(self, fn, **kwargs) -> str:
        last_err = None
        started = time.perf_counter()
        for attempt in range(self.retry_count):
            try:
                result = fn(**kwargs)
                self.last_retry_count = attempt
                self.last_latency_ms = (time.perf_counter() - started) * 1000
                return result
            except Exception as e:
                last_err = e
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay)
        self.last_retry_count = self.retry_count
        self.last_latency_ms = (time.perf_counter() - started) * 1000
        raise LLMError(f"API error after {self.retry_count} attempts: {last_err}")

    def generate(self, prompt: str, **kwargs) -> str:
        def _do():
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
            )
            self.last_usage = self._usage(resp)
            return resp.choices[0].message.content or ""
        return self._with_retry(_do)

    def generate_with_system(self, system: str, user: str, **kwargs) -> str:
        def _do():
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=kwargs.get("temperature", self.temperature),
                max_tokens=kwargs.get("max_tokens", self.max_tokens),
            )
            self.last_usage = self._usage(resp)
            return resp.choices[0].message.content or ""
        return self._with_retry(_do)

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


def get_agent_llm_client() -> OpenAIClient:
    return OpenAIClient(get_agent_llm_config())


def get_judge_llm_client() -> OpenAIClient:
    return OpenAIClient(get_judge_llm_config())
