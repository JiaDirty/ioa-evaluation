"""Read models that project immutable events/spans into UI graphs."""

from __future__ import annotations

from typing import Any

from src.orchestration.graph import ExecutionEdge, ExecutionGraph, ExecutionNode, StepStatus

from .models import ExecutionSpan, InteractionEdge


_STATUS_MAP = {
    "started": StepStatus.RUNNING,
    "running": StepStatus.RUNNING,
    "waiting": StepStatus.RUNNING,
    "completed": StepStatus.COMPLETED,
    "failed": StepStatus.FAILED,
    "skipped": StepStatus.SKIPPED,
    "cancelled": StepStatus.CANCELLED,
}


def project_execution_graph(task_id: str, trace_id: str, spans: list[ExecutionSpan],
                            base_graph: dict[str, Any] | None = None) -> ExecutionGraph:
    graph = ExecutionGraph.model_validate(base_graph) if base_graph else ExecutionGraph(task_id=task_id, trace_id=trace_id)
    has_base_graph = bool(graph.nodes)
    by_id = {node.node_id: node for node in graph.nodes}
    span_node_ids: dict[str, str] = {}
    for span in spans:
        node_id = span.node_id or span.span_id
        span_node_ids[span.span_id] = node_id
        node = by_id.get(node_id)
        if node is None and has_base_graph and not span.node_id:
            continue
        if node is None:
            node = ExecutionNode(
                node_id=node_id,
                node_type=_node_type(span),
                label=span.metadata.get("message") or span.operation or span.component_id or node_id,
                target_id=span.component_id or None,
            )
            graph.nodes.append(node)
            by_id[node_id] = node
        node.status = _STATUS_MAP.get(span.status, node.status)
        node.assigned_agent_id = span.component_id if span.component_type in {"domain_agent", "agent", "llm_runtime"} else node.assigned_agent_id
        node.input = span.input or node.input
        node.output = span.output or node.output
        node.error = span.error or node.error
        node.metadata.update({
            "span_id": span.span_id,
            "sequence": span.sequence,
            "component_type": span.component_type,
            "started_at": span.started_at.isoformat() if span.started_at else None,
            "ended_at": span.ended_at.isoformat() if span.ended_at else None,
            "duration_ms": span.duration_ms,
        })
    existing = {(edge.source, edge.target, edge.edge_type) for edge in graph.edges}
    for span in spans:
        if not span.parent_span_id or span.parent_span_id not in span_node_ids:
            continue
        edge = (span_node_ids[span.parent_span_id], span_node_ids[span.span_id], "calls")
        if edge not in existing and edge[0] != edge[1] and edge[0] in by_id and edge[1] in by_id:
            graph.edges.append(ExecutionEdge(source=edge[0], target=edge[1], edge_type=edge[2]))
            existing.add(edge)
    if not graph.edges:
        graph.refresh_edges()
    return graph


def build_interaction_edges(spans: list[ExecutionSpan]) -> list[InteractionEdge]:
    component_by_span = {span.span_id: span.component_id for span in spans if span.component_id}
    edges: list[InteractionEdge] = []
    seen: set[tuple[str, str, str, str]] = set()
    for span in spans:
        sources = span.upstream_ids or ([component_by_span.get(span.parent_span_id, "")] if span.parent_span_id else [])
        targets = span.downstream_ids or ([span.component_id] if span.component_id else [])
        relation = _relation(span)
        for source in filter(None, sources):
            for target in filter(None, targets):
                key = (source, target, relation, span.span_id)
                if source == target or key in seen:
                    continue
                seen.add(key)
                edges.append(InteractionEdge(
                    edge_id=f"edge-{len(edges) + 1}", source_id=source, target_id=target,
                    relation=relation, span_id=span.span_id, status=span.status,
                    sequence=span.sequence, protocol=str(span.metadata.get("protocol", "")),
                    message=str(span.metadata.get("message", "")), metadata={"operation": span.operation},
                ))
    return edges


def _node_type(span: ExecutionSpan) -> str:
    mapping = {
        "tool": "tool", "tool_gateway": "tool", "policy_engine": "policy_check",
        "human": "human", "synthesis": "synthesis", "delegation": "delegation",
    }
    return mapping.get(span.component_type, "agent_task" if "agent" in span.component_type else "verify")


def _relation(span: ExecutionSpan) -> str:
    operation = span.operation.lower()
    if "tool" in operation:
        return "tool_call"
    if "delegat" in operation:
        return "delegate"
    if "protocol" in operation or "deliver" in operation:
        return "message"
    if "artifact" in operation:
        return "artifact"
    return "call"
