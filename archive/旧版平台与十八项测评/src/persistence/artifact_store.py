"""Artifact stores."""

from __future__ import annotations

import json
from typing import Any

from .database import SQLiteDatabase


class ArtifactStore:
    def append(self, artifact: Any, trace_id: str = "") -> None:
        raise NotImplementedError

    def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError

    def list_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError


class MemoryArtifactStore(ArtifactStore):
    def __init__(self) -> None:
        self._rows: list[dict[str, Any]] = []

    def append(self, artifact: Any, trace_id: str = "") -> None:
        self._rows.append(_record_from_artifact(artifact, trace_id))

    def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        return [row for row in self._rows if row.get("task_id") == task_id]

    def list_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        return [row for row in self._rows if row.get("trace_id") == trace_id]


class SQLiteArtifactStore(ArtifactStore):
    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db
        self.db.init_schema()

    def append(self, artifact: Any, trace_id: str = "") -> None:
        record = _record_from_artifact(artifact, trace_id)
        with self.db.session() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO artifacts
                (artifact_id, task_id, trace_id, producer_agent_id, artifact_type,
                 content_json, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["artifact_id"],
                    record["task_id"],
                    record["trace_id"],
                    record["producer_agent_id"],
                    record["artifact_type"],
                    json.dumps(record["content"], ensure_ascii=False, default=str),
                    json.dumps(record["metadata"], ensure_ascii=False, default=str),
                    record["created_at"],
                ),
            )

    def list_by_task(self, task_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE task_id = ? ORDER BY created_at ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def list_by_trace(self, trace_id: str) -> list[dict[str, Any]]:
        with self.db.session() as conn:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE trace_id = ? ORDER BY created_at ASC",
                (trace_id,),
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    @staticmethod
    def _row_to_record(row) -> dict[str, Any]:
        return {
            "artifact_id": row["artifact_id"],
            "task_id": row["task_id"],
            "trace_id": row["trace_id"],
            "producer_agent_id": row["producer_agent_id"],
            "artifact_type": row["artifact_type"],
            "content": json.loads(row["content_json"] or "null"),
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at": row["created_at"],
        }


def _record_from_artifact(artifact: Any, trace_id: str = "") -> dict[str, Any]:
    data = artifact.model_dump(mode="json") if hasattr(artifact, "model_dump") else dict(artifact)
    return {
        "artifact_id": data.get("artifact_id", ""),
        "task_id": data.get("task_id", ""),
        "trace_id": trace_id or data.get("metadata", {}).get("trace_id", "") or data.get("task_id", ""),
        "producer_agent_id": data.get("producer_agent_id") or data.get("source_agent_id", ""),
        "artifact_type": data.get("artifact_type", ""),
        "content": data.get("content"),
        "metadata": data.get("metadata", {}),
        "created_at": data.get("created_at"),
    }
