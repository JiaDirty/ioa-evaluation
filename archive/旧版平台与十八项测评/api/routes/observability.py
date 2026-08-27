"""Unified system and task observability APIs."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from api.state import get_ioa_env
from src.observability import build_interaction_edges, project_execution_graph

router = APIRouter(prefix="/api", tags=["observability"])


@router.get("/system/graph")
async def get_system_graph(execution_mode: str = Query("offline_deterministic")) -> dict:
    env = await get_ioa_env(execution_mode)
    nodes: list[dict] = []
    edges: list[dict] = []

    def add_node(node_id: str, node_type: str, label: str, *, parent_id: str = "",
                 status: str = "active", metadata: dict | None = None) -> None:
        if any(node["id"] == node_id for node in nodes):
            return
        nodes.append({
            "id": node_id, "type": node_type, "label": label, "parent_id": parent_id,
            "status": status, "metadata": metadata or {},
        })

    def add_edge(source: str, target: str, relation: str) -> None:
        edges.append({"id": f"{source}:{relation}:{target}", "source": source, "target": target, "relation": relation})

    add_node("ioa", "ioa", "IoA Runtime")
    infrastructure = [
        ("marketplace", "marketplace", "Task Marketplace"),
        ("global-registry", "registry", "Global Registry"),
        ("protocol-router", "protocol", "Protocol Router"),
        ("shared-knowledge", "knowledge", "Shared Knowledge"),
        ("global-audit", "audit", "Global Audit"),
        ("synthesis-agent", "synthesis", "Synthesis Agent"),
        ("judge", "judge", "Judge"),
        ("human-checkpoint", "human", "Human Checkpoint"),
    ]
    for node_id, node_type, label in infrastructure:
        add_node(node_id, node_type, label, parent_id="ioa")
        add_edge("ioa", node_id, "contains")

    for sub_ioa_id in env.get_sub_ioa_ids():
        add_node(sub_ioa_id, "sub_ioa", f"{sub_ioa_id.title()} Sub-IoA", parent_id="ioa")
        add_edge("marketplace", sub_ioa_id, "routes")
        gateway = env.get_gateway(sub_ioa_id)
        gateway_id = gateway.gateway_id if gateway else f"{sub_ioa_id}-gw"
        registry_id = f"{sub_ioa_id}-registry"
        add_node(gateway_id, "gateway", f"{sub_ioa_id.title()} Gateway", parent_id=sub_ioa_id)
        add_node(registry_id, "registry", f"{sub_ioa_id.title()} Registry", parent_id=sub_ioa_id)
        add_edge(sub_ioa_id, gateway_id, "contains")
        add_edge(gateway_id, registry_id, "discovers")
        registry = env.get_local_registry(sub_ioa_id)
        if registry is not None:
            for agent in await registry.list_agents(sub_ioa_id):
                if agent.agent_id.endswith("-gw"):
                    continue
                runtime_type = "unbound"
                if env.runtime_manager.has_runtime(agent.agent_id):
                    runtime_type = env.runtime_manager.get_runtime(agent.agent_id).runtime_type
                add_node(
                    agent.agent_id, "agent", agent.display_name, parent_id=sub_ioa_id,
                    status=agent.status.value if hasattr(agent.status, "value") else str(agent.status),
                    metadata={
                        "capabilities": agent.declared_capabilities,
                        "runtime_type": runtime_type,
                        "protocols": [item.value for item in agent.supported_protocols],
                    },
                )
                add_edge(registry_id, agent.agent_id, "registers")
                add_edge(gateway_id, agent.agent_id, "dispatches")
        add_edge(gateway_id, "protocol-router", "negotiates")
        add_edge(gateway_id, "global-audit", "audits")

    for descriptor in env.tool_gateway.list_tools():
        tool_id = f"tool:{descriptor['tool_id']}"
        add_node(tool_id, "tool", descriptor.get("name") or descriptor["tool_id"], parent_id="ioa", metadata=descriptor)
        add_edge("ioa", tool_id, "provides")
    for server in env.mcp_server_registry.list_servers():
        server_data = server.model_dump(mode="json") if hasattr(server, "model_dump") else dict(server)
        server_id = f"mcp:{server_data['server_id']}"
        add_node(server_id, "mcp", server_data.get("name") or server_data["server_id"], parent_id="ioa", metadata=server_data)
        add_edge(server_id, "protocol-router", "connects")
    return {"nodes": nodes, "edges": edges, "execution_mode": execution_mode}


@router.get("/tasks/{task_id}/spans")
async def get_task_spans(task_id: str, after_sequence: int = 0) -> list[dict]:
    env = await get_ioa_env()
    if env.task_store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return [
        span.model_dump(mode="json")
        for span in env.observability_store.list_spans(task_id=task_id, after_sequence=after_sequence)
    ]


@router.get("/tasks/{task_id}/spans/{span_id}")
async def get_task_span(task_id: str, span_id: str) -> dict:
    env = await get_ioa_env()
    span = env.observability_store.get_span(span_id)
    if span is None or span.task_id != task_id:
        raise HTTPException(status_code=404, detail=f"Span not found: {span_id}")
    return {
        "span": span.model_dump(mode="json"),
        "payloads": env.observability_store.list_payloads(span_id),
    }


@router.get("/tasks/{task_id}/observability")
async def get_task_observability(task_id: str) -> dict:
    env = await get_ioa_env()
    record = env.task_store.get_task(task_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    spans = env.observability_store.list_spans(task_id=task_id)
    events = env.event_bus.query(task_id=task_id)
    base_graph = _latest_graph(events)
    graph = project_execution_graph(task_id, record.trace_id, spans, base_graph)
    return {
        "task": record.model_dump(mode="json"),
        "events": [event.model_dump(mode="json") for event in events],
        "spans": [span.model_dump(mode="json") for span in spans],
        "execution_graph": graph.model_dump(mode="json"),
        "interaction_edges": [edge.model_dump(mode="json") for edge in build_interaction_edges(spans)],
        "tool_calls": env.tool_call_store.list_by_trace(record.trace_id),
        "artifacts": env.artifact_store.list_by_trace(record.trace_id),
    }


def _latest_graph(events) -> dict | None:
    graph = None
    for event in events:
        payload = event.payload or {}
        candidate = payload.get("execution_graph") or payload.get("after_graph")
        if isinstance(candidate, dict) and candidate.get("nodes") is not None:
            graph = candidate
    return graph
