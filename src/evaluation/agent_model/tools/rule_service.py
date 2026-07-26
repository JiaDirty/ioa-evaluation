"""Rule service for norm drift evaluation.

Maintains a local rule registry with: formal rules, temporary exceptions,
and proposed shared-memory entries. Models can only propose writes;
the local service validates and applies them.
"""

from __future__ import annotations

from datetime import datetime
from copy import deepcopy
from typing import Any


class RuleService:
    """Local deterministic rule registry for norm drift testing."""

    def __init__(
        self,
        rules: list[dict[str, Any]] | None = None,
        exceptions: list[dict[str, Any]] | None = None,
    ):
        self._rules: dict[str, dict[str, Any]] = {}
        self._exceptions: dict[str, dict[str, Any]] = {}
        self._proposed_memories: list[dict[str, Any]] = []
        self._validated_memories: list[dict[str, Any]] = []

        for r in (rules or []):
            rid = r.get("rule_id", "")
            if rid:
                self._rules[rid] = dict(r)
        for e in (exceptions or []):
            eid = e.get("exception_id", "")
            if eid:
                self._exceptions[eid] = dict(e)

    def query_rule_status(self, rule_or_exception_id: str) -> dict[str, Any]:
        """Return current status of a rule or exception."""
        if rule_or_exception_id in self._rules:
            return {"type": "rule", **self._rules[rule_or_exception_id]}
        if rule_or_exception_id in self._exceptions:
            return {"type": "exception", **self._exceptions[rule_or_exception_id]}
        return {"type": "unknown", "id": rule_or_exception_id, "status": "not_found"}

    def get_active_rules(self, current_round: int = -1) -> list[dict[str, Any]]:
        """Return rules that are currently active."""
        active = []
        for r in self._rules.values():
            if r.get("status") == "active":
                active.append(r)
        return active

    def get_active_exceptions(self, current_round: int) -> list[dict[str, Any]]:
        """Return exceptions valid for the given round."""
        active = []
        for e in self._exceptions.values():
            valid_from = e.get("valid_from_round", -1)
            valid_until = e.get("valid_until_round", 9999)
            if valid_from <= current_round <= valid_until:
                active.append(e)
        return active

    def get_expired_exceptions(self, current_round: int) -> list[dict[str, Any]]:
        """Return exceptions that have expired by the given round."""
        expired = []
        for e in self._exceptions.values():
            valid_until = e.get("valid_until_round", 9999)
            if current_round > valid_until:
                e_copy = dict(e)
                e_copy["status"] = "expired"
                expired.append(e_copy)
        return expired

    def propose_memory_write(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Agent proposes a shared memory write. Local service decides."""
        entry = {
            "content": content,
            "metadata": metadata or {},
            "proposed_at": datetime.now().isoformat(),
            "status": "proposed",
            "entry_id": f"mem-prop-{len(self._proposed_memories):04d}",
        }
        self._proposed_memories.append(entry)
        return entry

    def validate_memory_proposal(
        self, entry_id: str, approved: bool, reason: str = ""
    ) -> dict[str, Any] | None:
        """Local service approves or rejects a proposed memory."""
        for entry in self._proposed_memories:
            if entry["entry_id"] == entry_id:
                entry["status"] = "validated" if approved else "rejected"
                entry["review_reason"] = reason
                if approved:
                    self._validated_memories.append(entry)
                return entry
        return None

    def get_validated_memories(self) -> list[dict[str, Any]]:
        return list(self._validated_memories)

    def invalidate_memory(self, entry_id: str) -> bool:
        for entry in self._validated_memories:
            if entry["entry_id"] == entry_id:
                entry["status"] = "invalidated"
                return True
        return False

    def list_memories(self) -> dict[str, Any]:
        return {
            "entries": [dict(entry) for entry in self._validated_memories],
            "semantic_success": True,
        }

    def export_state(self) -> dict[str, Any]:
        return {
            "rules": deepcopy(self._rules),
            "exceptions": deepcopy(self._exceptions),
            "proposed_memories": deepcopy(self._proposed_memories),
            "validated_memories": deepcopy(self._validated_memories),
        }

    def import_state(self, state: dict[str, Any]) -> None:
        self._rules = deepcopy(state.get("rules", self._rules))
        self._exceptions = deepcopy(state.get("exceptions", self._exceptions))
        self._proposed_memories = deepcopy(state.get("proposed_memories", []))
        self._validated_memories = deepcopy(state.get("validated_memories", []))
