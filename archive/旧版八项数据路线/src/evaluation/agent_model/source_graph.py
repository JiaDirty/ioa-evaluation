"""Source graph for consensus evaluation.

Tracks source relationships: which artifacts derived from which sources,
independence groups, controller groups, and authority levels.
"""

from __future__ import annotations

from typing import Any


class SourceGraph:
    """Source relationship graph for ecosystem consensus evaluation."""

    def __init__(self):
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[dict[str, Any]] = []

    def add_node(
        self,
        source_id: str,
        *,
        independence_group: str = "",
        controller_group: str | None = None,
        authority_level: str = "normal",
        verified: bool = False,
    ) -> None:
        self._nodes[source_id] = {
            "source_id": source_id,
            "independence_group": independence_group,
            "controller_group": controller_group,
            "authority_level": authority_level,
            "verified": verified,
        }

    def add_edge(self, from_id: str, to_id: str, relation: str = "derived_from") -> None:
        self._edges.append({"from": from_id, "to": to_id, "relation": relation})

    def get_independence_groups(self) -> dict[str, list[str]]:
        groups: dict[str, list[str]] = {}
        for sid, node in self._nodes.items():
            g = node.get("independence_group", "unknown")
            groups.setdefault(g, []).append(sid)
        return groups

    def count_distinct_sources(self, artifact_ids: list[str]) -> int:
        """Count how many distinct independence groups contributed."""
        groups = set()
        for aid in artifact_ids:
            node = self._nodes.get(aid, {})
            g = node.get("independence_group", aid)
            groups.add(g)
        return len(groups)

    def query_lineage(self, source_id: str) -> dict[str, Any]:
        node = self._nodes.get(source_id, {})
        derived = [e for e in self._edges if e.get("from") == source_id]
        return {
            "source_id": source_id,
            "node": node,
            "derived_artifacts": [e.get("to") for e in derived],
            "independence_group": node.get("independence_group", "unknown"),
        }
