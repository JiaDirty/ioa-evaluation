"""Serializable observability models used by persistence and the API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ExecutionSpan(BaseModel):
    span_id: str = Field(default_factory=lambda: f"span-{uuid.uuid4().hex[:12]}")
    parent_span_id: str | None = None
    sequence: int = 0
    task_id: str
    trace_id: str
    experiment_id: str = ""
    scenario_id: str = ""
    run_group: str = ""
    graph_id: str = ""
    node_id: str = ""
    span_type: str = "operation"
    component_type: str = ""
    component_id: str = ""
    operation: str = ""
    status: str = "pending"
    attempt: int = 1
    started_at: datetime | None = None
    ended_at: datetime | None = None
    duration_ms: float | None = None
    input: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    input_refs: list[str] = Field(default_factory=list)
    output_refs: list[str] = Field(default_factory=list)
    upstream_ids: list[str] = Field(default_factory=list)
    downstream_ids: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class ObservationPayload(BaseModel):
    payload_id: str = Field(default_factory=lambda: f"payload-{uuid.uuid4().hex[:12]}")
    task_id: str
    trace_id: str
    span_id: str
    direction: Literal["input", "output"]
    content: Any
    content_size: int = 0
    truncated: bool = False
    created_at: datetime = Field(default_factory=datetime.now)


class InteractionEdge(BaseModel):
    edge_id: str
    source_id: str
    target_id: str
    relation: str
    span_id: str
    status: str
    sequence: int
    protocol: str = ""
    message: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
