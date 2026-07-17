"""In-memory task event stream for interactive runtime views."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable

from pydantic import BaseModel, Field

from src.security.redaction import redact_sensitive


class TaskEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4())[:12])
    task_id: str
    trace_id: str
    sequence: int = 0
    span_id: str = ""
    parent_span_id: str | None = None
    experiment_id: str = ""
    scenario_id: str = ""
    run_group: str = ""
    graph_id: str = ""
    node_id: str = ""
    stage: str
    event_type: str
    operation: str = ""
    phase: str = "completed"
    actor_type: str = ""
    actor_id: str = ""
    message: str = ""
    status: str = "ok"
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
    error: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)

    @property
    def timestamp(self) -> datetime:
        return self.created_at

    @property
    def component(self) -> str:
        return self.actor_type


class EventBus:
    def __init__(self, store=None, observability_store=None, context: dict[str, str] | None = None) -> None:
        if store is None:
            from src.persistence.event_store import MemoryEventStore

            store = MemoryEventStore()
        self.store = store
        self.observability_store = observability_store
        self.context = context or {}
        self._hooks: list[Callable[[TaskEvent], TaskEvent | None]] = []

    def add_hook(self, hook: Callable[[TaskEvent], TaskEvent | None]) -> None:
        if hook not in self._hooks:
            self._hooks.append(hook)

    def remove_hook(self, hook: Callable[[TaskEvent], TaskEvent | None]) -> None:
        if hook in self._hooks:
            self._hooks.remove(hook)

    def emit(
        self,
        *,
        task_id: str,
        trace_id: str,
        stage: str,
        event_type: str,
        actor_type: str = "",
        actor_id: str = "",
        message: str = "",
        status: str = "ok",
        payload: dict[str, Any] | None = None,
        span_id: str | None = None,
        parent_span_id: str | None = None,
        graph_id: str = "",
        node_id: str = "",
        operation: str = "",
        phase: str | None = None,
        attempt: int = 1,
        started_at: datetime | None = None,
        ended_at: datetime | None = None,
        duration_ms: float | None = None,
        input: dict[str, Any] | None = None,
        output: dict[str, Any] | None = None,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        upstream_ids: list[str] | None = None,
        downstream_ids: list[str] | None = None,
        error: str | None = None,
    ) -> TaskEvent:
        created_at = datetime.now()
        resolved_payload = redact_sensitive(payload or {})
        resolved_phase = phase or self._infer_phase(event_type, status)
        resolved_span_id = span_id or f"span-{uuid.uuid4().hex[:12]}"
        terminal = resolved_phase in {"completed", "failed", "cancelled", "skipped"}
        event = TaskEvent(
            task_id=task_id,
            trace_id=trace_id,
            sequence=self.store.next_sequence(),
            span_id=resolved_span_id,
            parent_span_id=parent_span_id,
            experiment_id=self.context.get("experiment_id", ""),
            scenario_id=self.context.get("scenario_id", ""),
            run_group=self.context.get("run_group", ""),
            graph_id=graph_id or str(resolved_payload.get("graph_id", "")),
            node_id=node_id or str(resolved_payload.get("node_id", "")),
            stage=stage,
            event_type=event_type,
            operation=operation or event_type,
            phase=resolved_phase,
            actor_type=actor_type,
            actor_id=actor_id,
            message=message,
            status=status,
            attempt=attempt,
            started_at=started_at or created_at,
            ended_at=ended_at or (created_at if terminal else None),
            duration_ms=duration_ms,
            input=redact_sensitive(input or (resolved_payload if resolved_phase == "started" else {})),
            output=redact_sensitive(output or (resolved_payload if resolved_phase != "started" else {})),
            input_refs=input_refs or [],
            output_refs=output_refs or [],
            upstream_ids=upstream_ids or [],
            downstream_ids=downstream_ids or [],
            error=error,
            payload=resolved_payload,
            created_at=created_at,
        )
        self._persist_payloads(event)
        for hook in list(self._hooks):
            maybe_event = hook(event)
            if maybe_event is not None:
                event = maybe_event
        self.store.append(event)
        self._project_span(event)
        return event

    def query(self, trace_id: str | None = None, task_id: str | None = None,
              experiment_id: str | None = None, after_sequence: int = 0) -> list[TaskEvent]:
        if trace_id is not None:
            events = (
                self.store.list_by_trace(trace_id)
                if after_sequence == 0
                else self.store.list_after_sequence(after_sequence, trace_id=trace_id)
            )
        elif task_id is not None:
            events = (
                self.store.list_by_task(task_id)
                if after_sequence == 0
                else self.store.list_after_sequence(after_sequence, task_id=task_id)
            )
        elif experiment_id is not None:
            events = self.store.list_by_experiment(experiment_id, after_sequence)
        else:
            events = []
        return sorted(events, key=lambda event: (event.sequence, event.created_at))

    def start_span(self, **kwargs) -> TaskEvent:
        kwargs.setdefault("phase", "started")
        kwargs.setdefault("status", "running")
        return self.emit(**kwargs)

    def finish_span(self, *, span_id: str, **kwargs) -> TaskEvent:
        kwargs.setdefault("phase", "completed")
        kwargs.setdefault("status", "completed")
        if self.observability_store is not None:
            existing = self.observability_store.get_span(span_id)
            if existing is not None and existing.started_at is not None:
                ended_at = kwargs.get("ended_at") or datetime.now()
                kwargs.setdefault("started_at", existing.started_at)
                kwargs.setdefault("ended_at", ended_at)
                kwargs.setdefault(
                    "duration_ms",
                    max(0.0, (ended_at - existing.started_at).total_seconds() * 1000),
                )
        return self.emit(span_id=span_id, **kwargs)

    def clear(self) -> None:
        self.store.clear()

    @staticmethod
    def _infer_phase(event_type: str, status: str) -> str:
        lowered = f"{event_type} {status}".lower()
        if "waiting" in lowered or "input_required" in lowered:
            return "waiting"
        if "failed" in lowered or "error" in lowered or status.lower() in {"failed", "denied"}:
            return "failed"
        if "cancel" in lowered:
            return "cancelled"
        if "skip" in lowered:
            return "skipped"
        if "started" in lowered or status.lower() == "running":
            return "started"
        return "completed"

    def _persist_payloads(self, event: TaskEvent) -> None:
        if self.observability_store is None:
            return
        from src.observability.models import ObservationPayload

        for direction, content, refs in (
            ("input", event.input, event.input_refs),
            ("output", event.output, event.output_refs),
        ):
            if not content:
                continue
            payload = ObservationPayload(
                task_id=event.task_id,
                trace_id=event.trace_id,
                span_id=event.span_id,
                direction=direction,
                content=content,
            )
            refs.append(self.observability_store.save_payload(payload))

    def _project_span(self, event: TaskEvent) -> None:
        if self.observability_store is None:
            return
        from src.observability.models import ExecutionSpan

        span = ExecutionSpan(
            span_id=event.span_id,
            parent_span_id=event.parent_span_id,
            sequence=event.sequence,
            task_id=event.task_id,
            trace_id=event.trace_id,
            experiment_id=event.experiment_id,
            scenario_id=event.scenario_id,
            run_group=event.run_group,
            graph_id=event.graph_id,
            node_id=event.node_id,
            span_type=event.actor_type or "operation",
            component_type=event.actor_type,
            component_id=event.actor_id,
            operation=event.operation,
            status=event.phase,
            attempt=event.attempt,
            started_at=event.started_at,
            ended_at=event.ended_at,
            duration_ms=event.duration_ms,
            input=event.input,
            output=event.output,
            input_refs=event.input_refs,
            output_refs=event.output_refs,
            upstream_ids=event.upstream_ids,
            downstream_ids=event.downstream_ids,
            metadata={"stage": event.stage, "event_type": event.event_type, "message": event.message, **event.payload},
            error=event.error,
        )
        self.observability_store.upsert_span(span)
