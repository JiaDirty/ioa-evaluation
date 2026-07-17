"""Deterministic validation for agentic capability-level execution graphs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .graph import ExecutionGraph


@dataclass
class PlanValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


class PlanValidationError(ValueError):
    pass


class PlanValidator:
    forbidden_route_keys = {
        "agent_id",
        "assigned_agent_id",
        "endpoint",
        "certificate",
        "hop_chain",
        "target_sub_ioas",
    }

    def validate(
        self,
        graph: ExecutionGraph,
        *,
        max_nodes: int = 12,
        max_depth: int = 6,
    ) -> PlanValidationResult:
        errors: list[str] = []
        node_ids = [node.node_id for node in graph.nodes]
        node_id_set = set(node_ids)
        if len(node_ids) != len(node_id_set):
            errors.append("duplicate node_id in execution graph")
        if len(graph.nodes) > max_nodes:
            errors.append(f"plan node count exceeds limit: {len(graph.nodes)} > {max_nodes}")

        for node in graph.nodes:
            for dependency in node.depends_on:
                if dependency not in node_id_set:
                    errors.append(f"node {node.node_id} depends on missing node {dependency}")
            if node.node_type == "agent_task" and node.assigned_agent_id is not None:
                errors.append(f"agent_task node {node.node_id} pre-binds assigned_agent_id")
            raw = json.dumps(self._route_sensitive_payload(node.model_dump(mode="json")), ensure_ascii=False).lower()
            for key in self.forbidden_route_keys:
                if f'"{key.lower()}"' in raw:
                    errors.append(f"node {node.node_id} contains forbidden planner route key: {key}")

        has_cycle = self._has_cycle(graph)
        if has_cycle:
            errors.append("execution graph contains a cycle")
        else:
            depth = self._max_depth(graph)
            if depth > max_depth:
                errors.append(f"plan depth exceeds limit: {depth} > {max_depth}")
        if not any(node.node_type == "synthesis" for node in graph.nodes):
            errors.append("plan has no synthesis node")

        return PlanValidationResult(valid=not errors, errors=errors)

    def assert_valid(self, graph: ExecutionGraph, **kwargs: Any) -> None:
        result = self.validate(graph, **kwargs)
        if not result.valid:
            raise PlanValidationError("; ".join(result.errors))

    @classmethod
    def _route_sensitive_payload(cls, payload: dict[str, Any]) -> dict[str, Any]:
        scrubbed = dict(payload)
        # Runtime assignment fields are allowed after validation; during planning
        # they must remain null, so non-null is checked above. Remove null fields
        # to avoid rejecting the model schema itself.
        for key in ["assigned_agent_id", "assigned_sub_ioa_id", "target_id"]:
            if scrubbed.get(key) is None:
                scrubbed.pop(key, None)
        return scrubbed

    @staticmethod
    def _has_cycle(graph: ExecutionGraph) -> bool:
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {node.node_id: node for node in graph.nodes}

        def visit(node_id: str) -> bool:
            if node_id in visiting:
                return True
            if node_id in visited:
                return False
            visiting.add(node_id)
            node = by_id.get(node_id)
            if node is not None:
                for dependency in node.depends_on:
                    if visit(dependency):
                        return True
            visiting.remove(node_id)
            visited.add(node_id)
            return False

        return any(visit(node.node_id) for node in graph.nodes)

    @staticmethod
    def _max_depth(graph: ExecutionGraph) -> int:
        by_id = {node.node_id: node for node in graph.nodes}
        memo: dict[str, int] = {}

        def depth(node_id: str) -> int:
            if node_id in memo:
                return memo[node_id]
            node = by_id.get(node_id)
            if node is None or not node.depends_on:
                memo[node_id] = 1
            else:
                memo[node_id] = 1 + max(depth(dep) for dep in node.depends_on)
            return memo[node_id]

        return max((depth(node.node_id) for node in graph.nodes), default=0)
