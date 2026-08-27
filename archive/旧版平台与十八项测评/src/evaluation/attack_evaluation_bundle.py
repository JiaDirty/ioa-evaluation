"""Canonical attack evaluation bundle consumed by Judge agents."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class AttackEvaluationBundle(BaseModel):
    experiment: dict[str, Any] = Field(default_factory=dict)
    attack_specification: dict[str, Any] = Field(default_factory=dict)
    attack_injection: dict[str, Any] = Field(default_factory=dict)
    task_execution: dict[str, Any] = Field(default_factory=dict)
    events: dict[str, list[dict[str, Any]]] = Field(default_factory=dict)
    state_snapshots: dict[str, dict[str, Any]] = Field(default_factory=dict)
    evidence_index: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def all_evidence_ids(self) -> set[str]:
        ids = set(self.evidence_index)
        for log in self.attack_injection.get("logs", []) or []:
            evidence_id = log.get("evidence_id")
            if evidence_id:
                ids.add(str(evidence_id))
        for group in self.events.values():
            for event in group:
                event_id = event.get("event_id")
                if event_id:
                    ids.add(str(event_id))
        return ids
