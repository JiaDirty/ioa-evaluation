"""LLM configuration loader for IOA evaluation environment.

Two independent configs:
- AgentLLMConfig: for Sub-IoA agents (AG2 AssistantAgent)
- JudgeLLMConfig: for risk judges and attack generators
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


CONFIG_DIR = Path(__file__).parent.parent.parent / "config"

# Limits for the GPT-4o family used by this project.  The requested output
# setting may be lower, but it must never exceed the provider's documented
# maximum or leave the input outside the model context window.
DEFAULT_CONTEXT_WINDOW_TOKENS = 128_000
DEFAULT_MODEL_MAX_COMPLETION_TOKENS = 16_384


class ConfigNotFoundError(Exception):
    pass


@dataclass
class AgentLLMConfig:
    """LLM config for IOA Sub-IoA agents."""
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0.7
    top_p: float = 1.0
    max_completion_tokens: int = DEFAULT_MODEL_MAX_COMPLETION_TOKENS
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    model_max_completion_tokens: int = DEFAULT_MODEL_MAX_COMPLETION_TOKENS
    retry_count: int = 3
    retry_delay: float = 1.0
    timeout: int = 120

    def get_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            key = os.getenv(self.api_key_env)
            if key:
                return key
        raise ConfigNotFoundError(
            "No API key. Set api_key in config/agent_llm_config.yaml "
            "or set the env var specified in api_key_env."
        )

    def to_ag2_config(self) -> dict:
        """Convert to AG2 llm_config format."""
        cfg = {
            "model": self.model,
            "api_key": self.get_api_key(),
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_completion_tokens": self.max_completion_tokens,
        }
        if self.base_url:
            cfg["base_url"] = self.base_url
        cfg["timeout"] = self.timeout
        cfg["max_retries"] = max(0, self.retry_count - 1)
        return cfg


@dataclass
class JudgeLLMConfig:
    """LLM config for judges and attack generators (lower temperature)."""
    provider: str = "openai"
    model: str = "gpt-4o-mini"
    api_key: Optional[str] = None
    api_key_env: Optional[str] = None
    base_url: Optional[str] = None
    temperature: float = 0
    max_completion_tokens: int = 4096
    judge_temperature: float = 0.1
    judge_max_completion_tokens: int = 2048
    judge_max_input_bytes: int = 300000
    context_window_tokens: int = DEFAULT_CONTEXT_WINDOW_TOKENS
    model_max_completion_tokens: int = DEFAULT_MODEL_MAX_COMPLETION_TOKENS
    retry_count: int = 3
    retry_delay: float = 1.0
    timeout: int = 120

    def get_api_key(self) -> str:
        if self.api_key:
            return self.api_key
        if self.api_key_env:
            key = os.getenv(self.api_key_env)
            if key:
                return key
        raise ConfigNotFoundError(
            "No API key. Set api_key in config/judge_llm_config.yaml "
            "or set the env var specified in api_key_env."
        )


# Global singletons
_agent_config: Optional[AgentLLMConfig] = None
_judge_config: Optional[JudgeLLMConfig] = None


def load_agent_llm_config(path: Optional[str] = None) -> AgentLLMConfig:
    global _agent_config
    path = Path(path) if path else CONFIG_DIR / "agent_llm_config.yaml"
    if not path.exists():
        raise ConfigNotFoundError(f"Agent LLM config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _agent_config = AgentLLMConfig(
        provider=data.get("provider", "openai"),
        model=data.get("model", "gpt-4o-mini"),
        api_key=data.get("api_key"),
        api_key_env=data.get("api_key_env"),
        base_url=data.get("base_url"),
        temperature=data.get("temperature", 0.7),
        top_p=data.get("top_p", 1.0),
        max_completion_tokens=_token_setting(
            data,
            "max_completion_tokens",
            "max_tokens",
            DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
        ),
        context_window_tokens=int(
            data.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
        ),
        model_max_completion_tokens=int(
            data.get(
                "model_max_completion_tokens",
                DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
            )
        ),
        retry_count=data.get("retry_count", 3),
        retry_delay=data.get("retry_delay", 1.0),
        timeout=data.get("timeout", 120),
    )
    return _agent_config


def get_agent_llm_config() -> AgentLLMConfig:
    global _agent_config
    if _agent_config is None:
        _agent_config = load_agent_llm_config()
    return _agent_config


def load_judge_llm_config(path: Optional[str] = None) -> JudgeLLMConfig:
    global _judge_config
    path = Path(path) if path else CONFIG_DIR / "judge_llm_config.yaml"
    if not path.exists():
        raise ConfigNotFoundError(f"Judge LLM config not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _judge_config = JudgeLLMConfig(
        provider=data.get("provider", "openai"),
        model=data.get("model", "gpt-4o-mini"),
        api_key=data.get("api_key"),
        api_key_env=data.get("api_key_env"),
        base_url=data.get("base_url"),
        temperature=data.get("temperature", 0),
        max_completion_tokens=_token_setting(
            data, "max_completion_tokens", "max_tokens", 4096
        ),
        judge_temperature=data.get("judge_temperature", 0.1),
        judge_max_completion_tokens=_token_setting(
            data,
            "judge_max_completion_tokens",
            "judge_max_tokens",
            2048,
        ),
        judge_max_input_bytes=data.get("judge_max_input_bytes", 300000),
        context_window_tokens=int(
            data.get("context_window_tokens", DEFAULT_CONTEXT_WINDOW_TOKENS)
        ),
        model_max_completion_tokens=int(
            data.get(
                "model_max_completion_tokens",
                DEFAULT_MODEL_MAX_COMPLETION_TOKENS,
            )
        ),
        retry_count=data.get("retry_count", 3),
        retry_delay=data.get("retry_delay", 1.0),
        timeout=data.get("timeout", 120),
    )
    return _judge_config


def get_judge_llm_config() -> JudgeLLMConfig:
    global _judge_config
    if _judge_config is None:
        _judge_config = load_judge_llm_config()
    return _judge_config


def reset_configs():
    global _agent_config, _judge_config
    _agent_config = None
    _judge_config = None


def _token_setting(
    data: dict, new_name: str, legacy_name: str, default: int
) -> int:
    """Read the current setting while accepting older local YAML files."""
    if new_name in data:
        return int(data[new_name])
    if legacy_name in data:
        return int(data[legacy_name])
    return default
