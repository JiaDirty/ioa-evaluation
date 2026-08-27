"""Trace utilities for API serialization."""

from __future__ import annotations

from typing import Any

from .event_bus import TaskEvent


def serialize_event(event: TaskEvent) -> dict[str, Any]:
    return event.model_dump(mode="json")
