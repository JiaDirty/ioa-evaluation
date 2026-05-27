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
    max_tokens: int = 4096

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
        }
        if self.base_url:
            cfg["base_url"] = self.base_url
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
    max_tokens: int = 4096
    judge_temperature: float = 0.1
    judge_max_tokens: int = 500
    retry_count: int = 3
    retry_delay: float = 1.0
    timeout: int = 30

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
_agent_model_configs: Optional[dict[str, AgentLLMConfig]] = None


def load_agent_model_configs(path: Optional[str] = None) -> dict[str, AgentLLMConfig]:
    """Load per-agent model configs from agent_model_configs.yaml.

    Each Sub-IoA can have its own model, temperature, etc.
    Falls back to agent_llm_config.yaml defaults for missing fields.
    """
    global _agent_model_configs
    path = Path(path) if path else CONFIG_DIR / "agent_model_configs.yaml"
    if not path.exists():
        _agent_model_configs = {}
        return _agent_model_configs

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    base = get_agent_llm_config()
    configs = {}
    for sub_ioa_id, overrides in data.items():
        if not isinstance(overrides, dict):
            continue
        configs[sub_ioa_id] = AgentLLMConfig(
            provider=overrides.get("provider", base.provider),
            model=overrides.get("model", base.model),
            api_key=overrides.get("api_key", base.api_key),
            api_key_env=overrides.get("api_key_env", base.api_key_env),
            base_url=overrides.get("base_url", base.base_url),
            temperature=overrides.get("temperature", base.temperature),
            max_tokens=overrides.get("max_tokens", base.max_tokens),
        )

    _agent_model_configs = configs
    return _agent_model_configs


def get_agent_model_config(sub_ioa_id: str) -> AgentLLMConfig:
    """Get LLM config for a specific Sub-IoA agent.

    Falls back to default agent config if no per-agent config exists.
    """
    global _agent_model_configs
    if _agent_model_configs is None:
        _agent_model_configs = load_agent_model_configs()
    return _agent_model_configs.get(sub_ioa_id, get_agent_llm_config())


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
        max_tokens=data.get("max_tokens", 4096),
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
        max_tokens=data.get("max_tokens", 4096),
        judge_temperature=data.get("judge_temperature", 0.1),
        judge_max_tokens=data.get("judge_max_tokens", 500),
        retry_count=data.get("retry_count", 3),
        retry_delay=data.get("retry_delay", 1.0),
        timeout=data.get("timeout", 30),
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
