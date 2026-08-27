"""MCP server registry loaded from YAML configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import MCPServerConfig

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_MCP_SERVERS_PATH = CONFIG_DIR / "mcp_servers.yaml"


class MCPServerRegistry:
    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}

    @classmethod
    def from_yaml(cls, path: str | Path = DEFAULT_MCP_SERVERS_PATH) -> "MCPServerRegistry":
        registry = cls()
        config_path = Path(path)
        if not config_path.exists():
            return registry
        with config_path.open("r", encoding="utf-8") as fh:
            loaded: Any = yaml.safe_load(fh) or {}
        raw_servers = loaded.get("servers", loaded) if isinstance(loaded, dict) else loaded
        if raw_servers is None:
            return registry
        if not isinstance(raw_servers, list):
            raise ValueError(f"MCP config must contain a list of servers: {config_path}")
        for raw in raw_servers:
            registry.register(MCPServerConfig(**raw))
        return registry

    def register(self, config: MCPServerConfig) -> None:
        self._servers[config.server_id] = config

    def get(self, server_id: str) -> MCPServerConfig:
        if server_id not in self._servers:
            raise KeyError(f"unknown MCP server: {server_id}")
        return self._servers[server_id]

    def list_servers(self, include_disabled: bool = True) -> list[MCPServerConfig]:
        servers = self._servers.values()
        if not include_disabled:
            servers = [server for server in servers if server.enabled]
        return sorted(servers, key=lambda item: item.server_id)

    def list_enabled_servers(self) -> list[MCPServerConfig]:
        return self.list_servers(include_disabled=False)
