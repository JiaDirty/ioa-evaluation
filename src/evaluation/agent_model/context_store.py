"""AgentContextStore — local SQLite-backed context persistence.

Every tested agent role's history is persisted locally keyed by
(run_id, role_id). No reliance on remote model memory — the local
runtime replays relevant history into each API call.

Schema tables:
  - agent_sessions: one row per (run_id, role_id, variant)
  - agent_turns:   each turn's input/output/tool_calls/artifacts
  - risk_run_state: per-run shared state (rules, rewards, user state, etc.)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import VARIANT
from .event_log import EvaluationEvent

logger = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS agent_sessions (
    session_id   TEXT PRIMARY KEY,
    run_id       TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    variant      TEXT NOT NULL,
    role_id      TEXT NOT NULL,
    agent_id     TEXT DEFAULT '',
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_turns (
    turn_id           TEXT PRIMARY KEY,
    session_id        TEXT NOT NULL REFERENCES agent_sessions(session_id),
    round_index       INTEGER NOT NULL DEFAULT 0,
    input_json        TEXT NOT NULL DEFAULT '{}',
    output_json       TEXT NOT NULL DEFAULT '{}',
    tool_calls_json   TEXT NOT NULL DEFAULT '[]',
    artifact_refs_json TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS risk_run_state (
    run_id     TEXT PRIMARY KEY,
    case_id    TEXT NOT NULL,
    risk_type  TEXT NOT NULL,
    variant    TEXT NOT NULL,
    state_json TEXT NOT NULL DEFAULT '{}',
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS evaluation_events (
    event_id     TEXT PRIMARY KEY,
    schema_version TEXT NOT NULL DEFAULT '1.0',
    sequence     INTEGER NOT NULL DEFAULT 0,
    idempotency_key TEXT NOT NULL DEFAULT '',
    parent_event_ids_json TEXT NOT NULL DEFAULT '[]',
    caused_by_event_id TEXT,
    run_id       TEXT NOT NULL,
    case_id      TEXT NOT NULL,
    variant      TEXT NOT NULL,
    repeat_index INTEGER NOT NULL DEFAULT 0,
    role_id      TEXT DEFAULT '',
    round_index  INTEGER NOT NULL DEFAULT 0,
    event_type   TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    payload_hash TEXT NOT NULL DEFAULT '',
    source       TEXT NOT NULL DEFAULT 'agent_model',
    evidence_ref TEXT DEFAULT '',
    timestamp    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scenario_state_snapshots (
    snapshot_id       TEXT PRIMARY KEY,
    scenario_state_id TEXT NOT NULL,
    source_run_id     TEXT NOT NULL,
    case_id           TEXT NOT NULL,
    repeat_index      INTEGER NOT NULL DEFAULT 0,
    state_json        TEXT NOT NULL,
    event_ids_json    TEXT NOT NULL DEFAULT '[]',
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sessions_run_role
    ON agent_sessions(run_id, role_id);

CREATE INDEX IF NOT EXISTS idx_turns_session
    ON agent_turns(session_id);

CREATE INDEX IF NOT EXISTS idx_turns_session_round
    ON agent_turns(session_id, round_index);

CREATE INDEX IF NOT EXISTS idx_eval_events_run
    ON evaluation_events(run_id, event_type);

CREATE INDEX IF NOT EXISTS idx_scenario_snapshots_state
    ON scenario_state_snapshots(scenario_state_id, created_at);
"""


class AgentContextStore:
    """Local context persistence powered by SQLite."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def open(self) -> None:
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DDL)
        self._migrate_evaluation_events()
        self._conn.commit()
        logger.info("AgentContextStore opened at %s", self.db_path)

    def _migrate_evaluation_events(self) -> None:
        existing = {
            row["name"] for row in self.conn.execute("PRAGMA table_info(evaluation_events)")
        }
        additions = {
            "schema_version": "TEXT NOT NULL DEFAULT '1.0'",
            "sequence": "INTEGER NOT NULL DEFAULT 0",
            "idempotency_key": "TEXT NOT NULL DEFAULT ''",
            "parent_event_ids_json": "TEXT NOT NULL DEFAULT '[]'",
            "caused_by_event_id": "TEXT",
            "payload_hash": "TEXT NOT NULL DEFAULT ''",
        }
        for column, definition in additions.items():
            if column not in existing:
                self.conn.execute(
                    f"ALTER TABLE evaluation_events ADD COLUMN {column} {definition}"
                )
        self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_eval_events_idempotency "
            "ON evaluation_events(run_id, idempotency_key) WHERE idempotency_key != ''"
        )

    async def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("AgentContextStore not opened. Call open() first.")
        return self._conn

    # ------------------------------------------------------------------
    # Sessions
    # ------------------------------------------------------------------

    def upsert_session(
        self,
        run_id: str,
        case_id: str,
        variant: VARIANT,
        role_id: str,
        agent_id: str = "",
    ) -> str:
        """Get or create a session for (run_id, role_id). Returns session_id."""
        existing = self.get_session_id(run_id, role_id)
        if existing:
            self.conn.execute(
                "UPDATE agent_sessions SET updated_at=? WHERE session_id=?",
                (_now_iso(), existing),
            )
            self.conn.commit()
            return existing

        session_id = f"sess-{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            """INSERT INTO agent_sessions
               (session_id, run_id, case_id, variant, role_id, agent_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, run_id, case_id, variant, role_id, agent_id, _now_iso(), _now_iso()),
        )
        self.conn.commit()
        return session_id

    def get_session_id(self, run_id: str, role_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT session_id FROM agent_sessions WHERE run_id=? AND role_id=?",
            (run_id, role_id),
        ).fetchone()
        return row["session_id"] if row else None

    # ------------------------------------------------------------------
    # Turns
    # ------------------------------------------------------------------

    def append_turn(
        self,
        session_id: str,
        round_index: int,
        input_json: dict[str, Any],
        output_json: dict[str, Any],
        tool_calls_json: list[dict[str, Any]] | None = None,
        artifact_refs_json: list[str] | None = None,
    ) -> str:
        turn_id = f"turn-{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            """INSERT INTO agent_turns
               (turn_id, session_id, round_index, input_json, output_json,
                tool_calls_json, artifact_refs_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                turn_id,
                session_id,
                round_index,
                json.dumps(input_json, ensure_ascii=False, default=str),
                json.dumps(output_json, ensure_ascii=False, default=str),
                json.dumps(tool_calls_json or [], ensure_ascii=False, default=str),
                json.dumps(artifact_refs_json or [], ensure_ascii=False, default=str),
                _now_iso(),
            ),
        )
        self.conn.commit()
        return turn_id

    def get_recent_turns(
        self, session_id: str, limit: int = 5
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM agent_turns
               WHERE session_id=?
               ORDER BY round_index DESC, created_at DESC
               LIMIT ?""",
            (session_id, limit),
        ).fetchall()
        # Return in chronological order
        result = [_row_to_dict(r) for r in reversed(rows)]
        return result

    def get_all_turns(self, session_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            """SELECT * FROM agent_turns
               WHERE session_id=?
               ORDER BY round_index ASC, created_at ASC""",
            (session_id,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Run State
    # ------------------------------------------------------------------

    def get_run_state(self, run_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT state_json FROM risk_run_state WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return json.loads(row["state_json"])

    def update_run_state(self, run_id: str, patch: dict[str, Any]) -> None:
        current = self.get_run_state(run_id) or {}
        merged = {**current, **patch}
        self.conn.execute(
            """INSERT INTO risk_run_state (run_id, case_id, risk_type, variant, state_json, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(run_id) DO UPDATE SET
                   state_json=excluded.state_json,
                   updated_at=excluded.updated_at""",
            (
                run_id,
                patch.get("case_id", ""),
                patch.get("risk_type", ""),
                patch.get("variant", "baseline"),
                json.dumps(merged, ensure_ascii=False, default=str),
                patch.get("status", "active"),
                _now_iso(),
                _now_iso(),
            ),
        )
        self.conn.commit()

    def clear_run_state(self, run_id: str) -> None:
        self.conn.execute("DELETE FROM risk_run_state WHERE run_id=?", (run_id,))
        self.conn.execute(
            "DELETE FROM agent_turns WHERE session_id IN "
            "(SELECT session_id FROM agent_sessions WHERE run_id=?)",
            (run_id,),
        )
        self.conn.execute(
            "DELETE FROM agent_sessions WHERE run_id=?", (run_id,)
        )
        self.conn.commit()

    def create_scenario_snapshot(
        self,
        *,
        snapshot_id: str,
        scenario_state_id: str,
        source_run_id: str,
        case_id: str,
        repeat_index: int,
    ) -> dict[str, Any]:
        """Persist an immutable risk-state snapshot and its evidence frontier."""
        state = self.get_run_state(source_run_id) or {}
        event_ids = [event["event_id"] for event in self.list_events(source_run_id)]
        try:
            self.conn.execute(
                """INSERT INTO scenario_state_snapshots
                   (snapshot_id, scenario_state_id, source_run_id, case_id,
                    repeat_index, state_json, event_ids_json, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    snapshot_id,
                    scenario_state_id,
                    source_run_id,
                    case_id,
                    repeat_index,
                    json.dumps(state, ensure_ascii=False, default=str),
                    json.dumps(event_ids, ensure_ascii=False),
                    _now_iso(),
                ),
            )
            self.conn.commit()
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"scenario snapshot already exists: {snapshot_id}") from exc
        return self.get_scenario_snapshot(snapshot_id) or {}

    def get_scenario_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM scenario_state_snapshots WHERE snapshot_id=?",
            (snapshot_id,),
        ).fetchone()
        if row is None:
            return None
        value = dict(row)
        value["state"] = json.loads(value.pop("state_json"))
        value["event_ids"] = json.loads(value.pop("event_ids_json"))
        return value

    def initialize_run_from_snapshot(
        self,
        *,
        run_id: str,
        snapshot_id: str,
        variant: VARIANT,
    ) -> dict[str, Any]:
        """Create recovery run state from a snapshot without modifying it."""
        snapshot = self.get_scenario_snapshot(snapshot_id)
        if snapshot is None:
            raise KeyError(f"unknown scenario snapshot: {snapshot_id}")
        inherited = dict(snapshot["state"])
        inherited.update({
            "run_id": run_id,
            "variant": variant,
            "status": "running",
            "parent_snapshot_id": snapshot_id,
            "scenario_state_id": snapshot["scenario_state_id"],
            "inherited_event_ids": list(snapshot["event_ids"]),
        })
        self.update_run_state(run_id, inherited)
        return inherited

    # ------------------------------------------------------------------
    # Evaluation Events
    # ------------------------------------------------------------------

    def append_event(self, event: EvaluationEvent) -> str:
        """Append an event idempotently and preserve its causal sequence."""
        return self._append_event(event, commit=True)

    def _append_event(self, event: EvaluationEvent, *, commit: bool) -> str:
        idempotency_key = event.idempotency_key or event.event_id
        existing = self.conn.execute(
            "SELECT event_id FROM evaluation_events WHERE run_id=? AND idempotency_key=?",
            (event.run_id, idempotency_key),
        ).fetchone()
        if existing:
            return str(existing["event_id"])
        sequence = event.sequence
        if sequence is None:
            row = self.conn.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
                "FROM evaluation_events WHERE run_id=?",
                (event.run_id,),
            ).fetchone()
            sequence = int(row["next_sequence"])
        payload_json = json.dumps(
            event.payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        )
        self.conn.execute(
            """INSERT OR IGNORE INTO evaluation_events
               (event_id, schema_version, sequence, idempotency_key,
                parent_event_ids_json, caused_by_event_id, run_id, case_id,
                variant, repeat_index, role_id, round_index, event_type,
                payload_json, payload_hash, source, evidence_ref, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.event_id,
                event.schema_version,
                sequence,
                idempotency_key,
                json.dumps(event.parent_event_ids, sort_keys=True),
                event.caused_by_event_id,
                event.run_id,
                event.case_id,
                event.variant,
                event.repeat_index,
                event.role_id,
                event.round_index,
                event.event_type,
                payload_json,
                event.canonical_payload_hash(),
                event.source,
                event.evidence_ref,
                event.timestamp.isoformat(),
            ),
        )
        if commit:
            self.conn.commit()
        return event.event_id

    def update_run_state_with_event(
        self,
        run_id: str,
        patch: dict[str, Any],
        event: EvaluationEvent,
        *,
        inject_failure: bool = False,
    ) -> str:
        """Atomically persist a state transition and its audit event."""
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            current = self.get_run_state(run_id) or {}
            merged = {**current, **patch}
            now = _now_iso()
            self.conn.execute(
                """INSERT INTO risk_run_state
                   (run_id, case_id, risk_type, variant, state_json, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(run_id) DO UPDATE SET
                     state_json=excluded.state_json, status=excluded.status,
                     updated_at=excluded.updated_at""",
                (
                    run_id, patch.get("case_id", current.get("case_id", "")),
                    patch.get("risk_type", current.get("risk_type", "")),
                    patch.get("variant", current.get("variant", "baseline")),
                    json.dumps(merged, ensure_ascii=False, default=str),
                    patch.get("status", current.get("status", "active")), now, now,
                ),
            )
            if inject_failure:
                raise RuntimeError("injected failure before event append")
            event_id = self._append_event(event, commit=False)
            self.conn.commit()
            return event_id
        except Exception:
            self.conn.rollback()
            raise

    def verify_event_integrity(self, run_id: str) -> list[str]:
        """Return event ids whose stored payload no longer matches its hash."""
        invalid: list[str] = []
        rows = self.conn.execute(
            "SELECT event_id, payload_json, payload_hash FROM evaluation_events WHERE run_id=?",
            (run_id,),
        ).fetchall()
        for row in rows:
            digest = __import__("hashlib").sha256(row["payload_json"].encode("utf-8")).hexdigest()
            if not row["payload_hash"] or digest != row["payload_hash"]:
                invalid.append(str(row["event_id"]))
        return invalid

    def list_events(
        self,
        run_id: str,
        event_type: str | None = None,
    ) -> list[dict[str, Any]]:
        if event_type:
            rows = self.conn.execute(
                """SELECT * FROM evaluation_events
                   WHERE run_id=? AND event_type=?
                   ORDER BY round_index ASC, timestamp ASC""",
                (run_id, event_type),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT * FROM evaluation_events
                   WHERE run_id=?
                   ORDER BY round_index ASC, timestamp ASC""",
                (run_id,),
            ).fetchall()
        return [_event_row_to_dict(row) for row in rows]


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now().isoformat()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    # Deserialize JSON fields
    for field in ("input_json", "output_json", "tool_calls_json", "artifact_refs_json"):
        if field in d and isinstance(d[field], str):
            try:
                d[field] = json.loads(d[field])
            except json.JSONDecodeError:
                pass
    return d


def _event_row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    d = dict(row)
    try:
        d["payload"] = json.loads(d.pop("payload_json"))
    except json.JSONDecodeError:
        d["payload"] = {}
    if "parent_event_ids_json" in d:
        try:
            d["parent_event_ids"] = json.loads(d.pop("parent_event_ids_json"))
        except json.JSONDecodeError:
            d["parent_event_ids"] = []
    return d
