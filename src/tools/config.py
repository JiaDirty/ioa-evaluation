"""Tool descriptor configuration loading."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .models import ToolDescriptor

CONFIG_DIR = Path(__file__).resolve().parents[2] / "config"
DEFAULT_TOOLS_PATH = CONFIG_DIR / "tools.yaml"


def load_tool_descriptors(path: str | Path | None = None) -> list[ToolDescriptor]:
    """Load ToolDescriptor entries from YAML.

    Missing files return an empty list so local deterministic runs can fall back
    to in-code defaults.
    """
    config_path = Path(path) if path else DEFAULT_TOOLS_PATH
    if not config_path.exists():
        return []
    with config_path.open("r", encoding="utf-8") as fh:
        loaded: Any = yaml.safe_load(fh) or {}
    raw_tools = loaded.get("tools", loaded) if isinstance(loaded, dict) else loaded
    if not isinstance(raw_tools, list):
        raise ValueError(f"Tool config must contain a list of tools: {config_path}")
    return [ToolDescriptor(**item) for item in raw_tools]
