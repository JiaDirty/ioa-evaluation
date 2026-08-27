"""Task store implementations."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from .database import SQLiteDatabase
from .models import TaskRecord


class TaskStore:
    def create_task(self, task_record: TaskRecord | dict[str, Any]) -> TaskRecord:
        raise NotImplementedError

    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TaskRecord | None:
        raise NotImplementedError

    def get_task(self, task_id: str) -> TaskRecord | None:
        raise NotImplementedError

    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[TaskRecord]:
        raise NotImplementedError


class MemoryTaskStore(TaskStore):
    def __init__(self) -> None:
        self._tasks: dict[str, TaskRecord] = {}

    def create_task(self, task_record: TaskRecord | dict[str, Any]) -> TaskRecord:
        record = task_record if isinstance(task_record, TaskRecord) else TaskRecord(**task_record)
        self._tasks[record.task_id] = record
        return record

    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TaskRecord | None:
        record = self._tasks.get(task_id)
        if record is None:
            return None
        record = record.model_copy(
            update={
                "status": status,
                "result": result if result is not None else record.result,
                "error": error,
                "updated_at": datetime.now(),
            }
        )
        self._tasks[task_id] = record
        return record

    def get_task(self, task_id: str) -> TaskRecord | None:
        return self._tasks.get(task_id)

    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[TaskRecord]:
        records = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
        return records[offset: offset + limit]


class SQLiteTaskStore(TaskStore):
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        self.db.init_schema()

    def create_task(self, task_record: TaskRecord | dict[str, Any]) -> TaskRecord:
        record = task_record if isinstance(task_record, TaskRecord) else TaskRecord(**task_record)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO tasks
                (task_id, trace_id, status, description, payload_json, result_json, error, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.task_id,
                    record.trace_id,
                    record.status,
                    record.description,
                    json.dumps(record.payload, ensure_ascii=False, default=str),
                    json.dumps(record.result, ensure_ascii=False, default=str) if record.result is not None else None,
                    record.error,
                    record.created_at.isoformat(),
                    record.updated_at.isoformat(),
                ),
            )
        return record

    def update_task_status(
        self,
        task_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> TaskRecord | None:
        current = self.get_task(task_id)
        if current is None:
            return None
        updated = current.model_copy(
            update={
                "status": status,
                "result": result if result is not None else current.result,
                "error": error,
                "updated_at": datetime.now(),
            }
        )
        self.create_task(updated)
        return updated

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self.db.session() as conn:
            row = conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list_tasks(self, limit: int = 50, offset: int = 0) -> list[TaskRecord]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row) -> TaskRecord:
        return TaskRecord(
            task_id=row["task_id"],
            trace_id=row["trace_id"],
            status=row["status"],
            description=row["description"] or "",
            payload=json.loads(row["payload_json"] or "{}"),
            result=json.loads(row["result_json"]) if row["result_json"] else None,
            error=row["error"],
            created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else datetime.now(),
            updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else datetime.now(),
        )
