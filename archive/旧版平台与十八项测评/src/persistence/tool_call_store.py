"""Tool call history stores."""

from __future__ import annotations

import json
from typing import Any

from src.security.redaction import redact_sensitive

from .database import SQLiteDatabase


class ToolCallStore:
    def append_result(self, call: Any, result: Any) -> None:
        raise NotImplementedError

    def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        raise NotImplementedError


class MemoryToolCallStore(ToolCallStore):
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append_result(self, call: Any, result: Any) -> None:
        self._rows.append(_record_from_call_result(call, result))

    def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        return [row for row in self._rows if row.get("task_id") == task_id]

    def list_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        return [row for row in self._rows if row.get("trace_id") == trace_id]

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._rows[-limit:]))


class SQLiteToolCallStore(ToolCallStore):
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        self.db.init_schema()

    def append_result(self, call: Any, result: Any) -> None:
        record = _record_from_call_result(call, result)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tool_calls
                (call_id, task_id, trace_id, caller_agent_id, tool_id, status,
                 arguments_json, result_json, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["call_id"],
                    record["task_id"],
                    record["trace_id"],
                    record["caller_agent_id"],
                    record["tool_id"],
                    record["status"],
                    json.dumps(record["arguments"], ensure_ascii=False, default=str),
                    json.dumps(record["result"], ensure_ascii=False, default=str),
                    record["error"],
                    record["created_at"],
                ),
            )

    def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls WHERE trace_id = ? ORDER BY created_at ASC",
                (trace_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM tool_calls ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row) -> dict[str, Any]:
        return {
            "call_id": row["call_id"],
            "task_id": row["task_id"],
            "trace_id": row["trace_id"],
            "caller_agent_id": row["caller_agent_id"],
            "tool_id": row["tool_id"],
            "status": row["status"],
            "arguments": json.loads(row["arguments_json"] or "{}"),
            "result": json.loads(row["result_json"] or "{}"),
            "error": row["error"],
            "created_at": row["created_at"],
        }


def _record_from_call_result(call: Any, result: Any) -> dict[str, Any]:
    return {
        "call_id": call.call_id,
        "task_id": call.task_id,
        "trace_id": call.trace_id,
        "caller_agent_id": call.caller_agent_id,
        "tool_id": call.tool_id,
        "status": result.status,
        "arguments": redact_sensitive(call.arguments),
        "result": redact_sensitive(result.model_dump(mode="json")),
        "error": result.error,
        "created_at": result.created_at.isoformat(),
    }
