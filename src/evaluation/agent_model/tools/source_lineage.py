"""Source lineage query tool for ecosystem consensus evaluation.

Traverses the source_graph to answer queries about source independence,
derivation chains, and authority levels.
"""

from __future__ import annotations

from typing import Any


class SourceLineageQuery:
    """Query source relationships from the case's source_graph fixture."""

    def __init__(self, source_graph: list[dict[str, Any]] | None = None):
        self._graph: dict[str, dict[str, Any]] = {}
        if source_graph:
            for node in source_graph:
                sid = node.get("source_id", "")
                if sid:
                    self._graph[sid] = node

    def query_source_lineage(self, source_or_artifact_id: str) -> dict[str, Any]:
        """Return lineage info for a given source or artifact."""
        node = self._graph.get(source_or_artifact_id)
        if node:
            return dict(node)
        return {
            "source_id": source_or_artifact_id,
            "derived_from": [],
            "independence_group": "unknown",
            "authority_level": "unknown",
            "note": "source not in fixture graph",
        }

    def get_independence_groups(self) -> set[str]:
        """Return all known independence groups."""
        groups = set()
        for node in self._graph.values():
            g = node.get("independence_group", "")
            if g:
                groups.add(g)
        return groups
