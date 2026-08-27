"""SQLite and memory stores for execution spans and observable payloads."""

from __future__ import annotations

import json
from typing import Any

from src.observability.models import ExecutionSpan, ObservationPayload
from src.security.redaction import redact_sensitive

from .database import SQLiteDatabase


class SQLiteObservabilityStore:
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        self.db.init_schema()

    def upsert_span(self, span: ExecutionSpan) -> None:
        data = span.model_dump(mode="json")
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT INTO execution_spans (
                  span_id, parent_span_id, sequence, task_id, trace_id, experiment_id,
                  scenario_id, run_group, graph_id, node_id, span_type, component_type,
                  component_id, operation, status, attempt, started_at, ended_at,
                  duration_ms, input_json, output_json, input_refs_json, output_refs_json,
                  upstream_ids_json, downstream_ids_json, metadata_json, error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(span_id) DO UPDATE SET
                  sequence=excluded.sequence,
                  status=excluded.status,
                  ended_at=COALESCE(excluded.ended_at, execution_spans.ended_at),
                  duration_ms=COALESCE(excluded.duration_ms, execution_spans.duration_ms),
                  output_json=CASE WHEN excluded.output_json != '{}' THEN excluded.output_json ELSE execution_spans.output_json END,
                  output_refs_json=CASE WHEN excluded.output_refs_json != '[]' THEN excluded.output_refs_json ELSE execution_spans.output_refs_json END,
                  downstream_ids_json=CASE WHEN excluded.downstream_ids_json != '[]' THEN excluded.downstream_ids_json ELSE execution_spans.downstream_ids_json END,
                  metadata_json=excluded.metadata_json,
                  error=COALESCE(excluded.error, execution_spans.error)
                """,
                (
                    data["span_id"], data.get("parent_span_id"), data["sequence"], data["task_id"],
                    data["trace_id"], data.get("experiment_id", ""), data.get("scenario_id", ""),
                    data.get("run_group", ""), data.get("graph_id", ""), data.get("node_id", ""),
                    data.get("span_type", "operation"), data.get("component_type", ""),
                    data.get("component_id", ""), data.get("operation", ""), data.get("status", "pending"),
                    data.get("attempt", 1), data.get("started_at"), data.get("ended_at"),
                    data.get("duration_ms"), self._json(data.get("input", {})), self._json(data.get("output", {})),
                    self._json(data.get("input_refs", [])), self._json(data.get("output_refs", [])),
                    self._json(data.get("upstream_ids", [])), self._json(data.get("downstream_ids", [])),
                    self._json(data.get("metadata", {})), data.get("error"),
                ),
            )

    def list_spans(self, *, task_id: str | None = None, trace_id: str | None = None,
                   experiment_id: str | None = None, after_sequence: int = 0) -> list[ExecutionSpan]:
        field, value = self._scope(task_id, trace_id, experiment_id)
        with self.db.session() as conn:
            rows = conn.execute(
                f"SELECT * FROM execution_spans WHERE {field} = ? AND sequence > ? ORDER BY sequence ASC",
                (value, after_sequence),
            ).fetchall()
        return [self._row_to_span(row) for row in rows]

    def get_span(self, span_id: str) -> ExecutionSpan | None:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM execution_spans WHERE span_id = ?", (span_id,)).fetchone()
        return self._row_to_span(row) if row else None

    def save_payload(self, payload: ObservationPayload) -> str:
        data = payload.model_dump(mode="json")
        content = redact_sensitive(data["content"])
        raw = self._json(content)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO observation_payloads
                (payload_id, task_id, trace_id, span_id, direction, content_json,
                 content_size, truncated, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (data["payload_id"], data["task_id"], data["trace_id"], data["span_id"],
                 data["direction"], raw, len(raw.encode("utf-8")), int(data.get("truncated", False)),
                 data["created_at"]),
            )
        return payload.payload_id

    def list_payloads(self, span_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM observation_payloads WHERE span_id = ? ORDER BY created_at ASC", (span_id,)
            ).fetchall()
        return [{
            "payload_id": row["payload_id"], "task_id": row["task_id"], "trace_id": row["trace_id"],
            "span_id": row["span_id"], "direction": row["direction"],
            "content": json.loads(row["content_json"] or "null"), "content_size": row["content_size"],
            "truncated": bool(row["truncated"]), "created_at": row["created_at"],
        } for row in rows]

    @staticmethod
    def _scope(task_id: str | None, trace_id: str | None, experiment_id: str | None) -> tuple[str, str]:
        if task_id:
            return "task_id", task_id
        if trace_id:
            return "trace_id", trace_id
        if experiment_id:
            return "experiment_id", experiment_id
        raise ValueError("task_id, trace_id, or experiment_id is required")

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(redact_sensitive(value), ensure_ascii=False, default=str)

    @classmethod
    def _row_to_span(cls, row) -> ExecutionSpan:
        return ExecutionSpan(
            span_id=row["span_id"], parent_span_id=row["parent_span_id"], sequence=row["sequence"],
            task_id=row["task_id"], trace_id=row["trace_id"], experiment_id=row["experiment_id"] or "",
            scenario_id=row["scenario_id"] or "", run_group=row["run_group"] or "",
            graph_id=row["graph_id"] or "", node_id=row["node_id"] or "", span_type=row["span_type"] or "operation",
            component_type=row["component_type"] or "", component_id=row["component_id"] or "",
            operation=row["operation"] or "", status=row["status"] or "pending", attempt=row["attempt"] or 1,
            started_at=row["started_at"], ended_at=row["ended_at"], duration_ms=row["duration_ms"],
            input=json.loads(row["input_json"] or "{}"), output=json.loads(row["output_json"] or "{}"),
            input_refs=json.loads(row["input_refs_json"] or "[]"), output_refs=json.loads(row["output_refs_json"] or "[]"),
            upstream_ids=json.loads(row["upstream_ids_json"] or "[]"),
            downstream_ids=json.loads(row["downstream_ids_json"] or "[]"),
            metadata=json.loads(row["metadata_json"] or "{}"), error=row["error"],
        )
