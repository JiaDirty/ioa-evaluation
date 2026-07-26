"""Tool descriptor and callable registry."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .models import ToolDescriptor


class ToolRegistry:
    def __init__(self) -> None:
        self._descriptors: dict[str, ToolDescriptor] = {}
        self._handlers: dict[str, Callable[..., Any]] = {}

    def register(self, descriptor: ToolDescriptor, handler: Callable[..., Any] | None = None) -> None:
        self._descriptors[descriptor.tool_id] = descriptor
        if handler is not None:
            self._handlers[descriptor.tool_id] = handler

    def get(self, tool_id: str) -> ToolDescriptor | None:
        return self._descriptors.get(tool_id)

    def get_handler(self, tool_id: str) -> Callable[..., Any] | None:
        return self._handlers.get(tool_id)

    def list_tools(self) -> list[ToolDescriptor]:
        return sorted(self._descriptors.values(), key=lambda item: item.tool_id)

    def unregister(self, tool_id: str) -> None:
        """Remove both descriptor and handler for an explicitly scoped tool."""
        self._descriptors.pop(tool_id, None)
        self._handlers.pop(tool_id, None)
