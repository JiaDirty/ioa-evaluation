"""Observed artifact lineage derived from append-only events."""
from __future__ import annotations

from typing import Any


class ArtifactDAG:
    def __init__(self, events: list[dict[str, Any]]):
        self.nodes = {
            str(event.get("payload", {}).get("artifact_id")): event.get("payload", {})
            for event in events
            if event.get("event_type") == "artifact"
            and event.get("payload", {}).get("artifact_id")
        }

    def edges(self) -> list[tuple[str, str]]:
        return [
            (str(parent), artifact_id)
            for artifact_id, payload in self.nodes.items()
            for parent in payload.get("parent_artifact_ids", [])
            if parent
        ]

    def primary_artifact_ids(self) -> list[str]:
        return sorted(
            artifact_id
            for artifact_id, payload in self.nodes.items()
            if payload.get("primary") is True
        )

    def max_depth(self) -> int:
        parents = {
            artifact_id: list(payload.get("parent_artifact_ids", []))
            for artifact_id, payload in self.nodes.items()
        }
        cache: dict[str, int] = {}

        def depth(node: str, active: set[str]) -> int:
            if node in cache:
                return cache[node]
            if node in active:
                raise ValueError("artifact lineage contains a cycle")
            known_parents = [parent for parent in parents.get(node, []) if parent in parents]
            value = 0 if not known_parents else 1 + max(
                depth(parent, active | {node}) for parent in known_parents
            )
            cache[node] = value
            return value

        return max((depth(node, set()) for node in parents), default=0)
