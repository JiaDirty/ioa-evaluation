"""Append-only local event log for Agent Model v2 evaluation.

The formal scoring path must rely on observed events, tool results, artifacts,
and local state transitions.  This module provides a small deterministic event
contract that can be persisted in SQLite and consumed by feature extraction.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .models import VARIANT


EvaluationEventType = Literal[
    "agent_call",
    "model_call",
    "tool_call",
    "tool_result",
    "artifact",
    "message_forward",
    "board_action",
    "business_action",
    "reward",
    "user_state",
    "gateway_decision",
    "memory",
    "recovery",
    "judge",
]


class EvaluationEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    schema_version: str = "1.0"
    sequence: int | None = None
    idempotency_key: str = ""
    parent_event_ids: list[str] = Field(default_factory=list)
    caused_by_event_id: str | None = None
    run_id: str
    case_id: str
    variant: VARIANT
    repeat_index: int = 0
    role_id: str = ""
    round_index: int = 0
    event_type: EvaluationEventType
    payload: dict[str, Any] = Field(default_factory=dict)
    source: str = "agent_model"
    evidence_ref: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def canonical_payload_hash(self) -> str:
        payload = json.dumps(
            self.payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def make_event_id(
    *,
    run_id: str,
    case_id: str,
    variant: str,
    event_type: str,
    role_id: str = "",
    round_index: int = 0,
    payload: dict[str, Any] | None = None,
) -> str:
    """Build a stable idempotency key for an event payload."""
    raw = json.dumps(
        {
            "run_id": run_id,
            "case_id": case_id,
            "variant": variant,
            "event_type": event_type,
            "role_id": role_id,
            "round_index": round_index,
            "payload": payload or {},
        },
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return "ev-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
