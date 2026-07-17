"""SQLite persistence bootstrap."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from collections.abc import Iterator


class SQLiteDatabase:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def session(self) -> Iterator[sqlite3.Connection]:
        conn = self.connect()
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.session() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                  task_id TEXT PRIMARY KEY,
                  trace_id TEXT NOT NULL,
                  status TEXT NOT NULL,
                  description TEXT,
                  payload_json TEXT,
                  result_json TEXT,
                  error TEXT,
                  created_at TEXT,
                  updated_at TEXT
                );

                CREATE TABLE IF NOT EXISTS events (
                  event_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  trace_id TEXT NOT NULL,
                  stage TEXT,
                  event_type TEXT,
                  actor_type TEXT,
                  actor_id TEXT,
                  message TEXT,
                  status TEXT,
                  event_json TEXT,
                  created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS observability_sequence (
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT
                );

                CREATE TABLE IF NOT EXISTS execution_spans (
                  span_id TEXT PRIMARY KEY,
                  parent_span_id TEXT,
                  sequence INTEGER NOT NULL DEFAULT 0,
                  task_id TEXT NOT NULL,
                  trace_id TEXT NOT NULL,
                  experiment_id TEXT,
                  scenario_id TEXT,
                  run_group TEXT,
                  graph_id TEXT,
                  node_id TEXT,
                  span_type TEXT,
                  component_type TEXT,
                  component_id TEXT,
                  operation TEXT,
                  status TEXT,
                  attempt INTEGER NOT NULL DEFAULT 1,
                  started_at TEXT,
                  ended_at TEXT,
                  duration_ms REAL,
                  input_json TEXT,
                  output_json TEXT,
                  input_refs_json TEXT,
                  output_refs_json TEXT,
                  upstream_ids_json TEXT,
                  downstream_ids_json TEXT,
                  metadata_json TEXT,
                  error TEXT
                );

                CREATE TABLE IF NOT EXISTS observation_payloads (
                  payload_id TEXT PRIMARY KEY,
                  task_id TEXT NOT NULL,
                  trace_id TEXT NOT NULL,
                  span_id TEXT NOT NULL,
                  direction TEXT NOT NULL,
                  content_json TEXT,
                  content_size INTEGER NOT NULL DEFAULT 0,
                  truncated INTEGER NOT NULL DEFAULT 0,
                  created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS tool_calls (
                  call_id TEXT PRIMARY KEY,
                  task_id TEXT,
                  trace_id TEXT,
                  caller_agent_id TEXT,
                  tool_id TEXT,
                  status TEXT,
                  arguments_json TEXT,
                  result_json TEXT,
                  error TEXT,
                  created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS artifacts (
                  artifact_id TEXT PRIMARY KEY,
                  task_id TEXT,
                  trace_id TEXT,
                  producer_agent_id TEXT,
                  artifact_type TEXT,
                  content_json TEXT,
                  metadata_json TEXT,
                  created_at TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_events_trace_created
                  ON events(trace_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_spans_trace_sequence
                  ON execution_spans(trace_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_spans_task_sequence
                  ON execution_spans(task_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_spans_experiment_sequence
                  ON execution_spans(experiment_id, sequence);
                CREATE INDEX IF NOT EXISTS idx_payloads_span
                  ON observation_payloads(span_id);
                """
            )
            self._ensure_column(conn, "events", "sequence", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(conn, "events", "experiment_id", "TEXT")
            self._ensure_column(conn, "events", "span_id", "TEXT")

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
