"""Event store implementations used by EventBus."""

from __future__ import annotations

import json
from typing import Any

from .database import SQLiteDatabase


class EventStore:
    def next_sequence(self) -> int:
        raise NotImplementedError

    def append(self, event: Any) -> None:
        raise NotImplementedError

    def list_by_trace(self, trace_id: str) -> list[Any]:
        raise NotImplementedError

    def list_by_task(self, task_id: str) -> list[Any]:
        raise NotImplementedError

    def list_by_experiment(self, experiment_id: str, after_sequence: int = 0) -> list[Any]:
        raise NotImplementedError

    def list_after_sequence(self, sequence: int, *, task_id: str | None = None,
                            trace_id: str | None = None) -> list[Any]:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError


class MemoryEventStore(EventStore):
    def __init__(self) -> None:
        self._events: list[Any] = []
        self._sequence = 0

    def next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def append(self, event: Any) -> None:
        self._events.append(event)

    def list_by_trace(self, trace_id: str) -> list[Any]:
        return [event for event in self._events if event.trace_id == trace_id]

    def list_by_task(self, task_id: str) -> list[Any]:
        return [event for event in self._events if event.task_id == task_id]

    def list_by_experiment(self, experiment_id: str, after_sequence: int = 0) -> list[Any]:
        return [
            event for event in self._events
            if event.experiment_id == experiment_id and event.sequence > after_sequence
        ]

    def list_after_sequence(self, sequence: int, *, task_id: str | None = None,
                            trace_id: str | None = None) -> list[Any]:
        return [
            event for event in self._events
            if event.sequence > sequence
            and (task_id is None or event.task_id == task_id)
            and (trace_id is None or event.trace_id == trace_id)
        ]

    def clear(self) -> None:
        self._events.clear()
        self._sequence = 0


class SQLiteEventStore(EventStore):
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        self.db.init_schema()

    def next_sequence(self) -> int:
        with self.db.session() as conn:
            cursor = conn.execute("INSERT INTO observability_sequence DEFAULT VALUES")
            return int(cursor.lastrowid)

    def append(self, event: Any) -> None:
        data = event.model_dump(mode="json")
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO events
                (event_id, task_id, trace_id, stage, event_type, actor_type, actor_id,
                 message, status, event_json, created_at, sequence, experiment_id, span_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.event_id,
                    event.task_id,
                    event.trace_id,
                    event.stage,
                    event.event_type,
                    event.actor_type,
                    event.actor_id,
                    event.message,
                    event.status,
                    json.dumps(data, ensure_ascii=False, default=str),
                    data.get("created_at"),
                    data.get("sequence", 0),
                    data.get("experiment_id", ""),
                    data.get("span_id", ""),
                ),
            )

    def list_by_trace(self, trace_id: str) -> list[Any]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT event_json FROM events WHERE trace_id = ? ORDER BY sequence ASC, created_at ASC",
                (trace_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_by_task(self, task_id: str) -> list[Any]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT event_json FROM events WHERE task_id = ? ORDER BY sequence ASC, created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_by_experiment(self, experiment_id: str, after_sequence: int = 0) -> list[Any]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT event_json FROM events WHERE experiment_id = ? AND sequence > ? ORDER BY sequence ASC",
                (experiment_id, after_sequence),
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def list_after_sequence(self, sequence: int, *, task_id: str | None = None,
                            trace_id: str | None = None) -> list[Any]:
        clauses = ["sequence > ?"]
        params: list[Any] = [sequence]
        if task_id is not None:
            clauses.append("task_id = ?")
            params.append(task_id)
        if trace_id is not None:
            clauses.append("trace_id = ?")
            params.append(trace_id)
        with self.db.session() as conn:
            rows = conn.execute(
                f"SELECT event_json FROM events WHERE {' AND '.join(clauses)} ORDER BY sequence ASC",
                params,
            ).fetchall()
        return [self._row_to_event(row) for row in rows]

    def clear(self) -> None:
        with self.db.session() as conn:
            conn.execute("DELETE FROM events")
            conn.execute("DELETE FROM observability_sequence")

    @staticmethod
    def _row_to_event(row) -> Any:
        from src.audit.event_bus import TaskEvent

        return TaskEvent(**json.loads(row["event_json"]))
