"""Agent 拓扑 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/agents", tags=["agents"])

_current_topology = {"style": "full_mesh", "edges": []}


def _build_topology(style: str) -> dict:
    nodes = ["finance", "healthcare", "travel", "news"]
    edges = []
    if style == "full_mesh":
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                edges.append({"source": a, "target": b})
    elif style == "star":
        center = nodes[0]
        for b in nodes[1:]:
            edges.append({"source": center, "target": b})
    elif style == "chain":
        for i in range(len(nodes) - 1):
            edges.append({"source": nodes[i], "target": nodes[i + 1]})
    return {"style": style, "nodes": nodes, "edges": edges}


@router.get("/sub-ioas")
async def list_sub_ioas() -> list[dict]:
    """列出所有 Sub-IoA 及其 Agent 信息。"""
    from src.agents.ioa_agent import SUB_IOA_AGENT_CONFIGS
    result = []
    for sub_ioa_id, cfg in SUB_IOA_AGENT_CONFIGS.items():
        result.append({
            "id": sub_ioa_id,
            "name": cfg["display_name"],
            "agent_name": cfg["name"],
            "capabilities": cfg["capabilities"],
        })
    return result


@router.get("/topology")
async def get_topology() -> dict:
    """获取当前拓扑结构。"""
    return _build_topology(_current_topology["style"])


@router.put("/topology")
async def update_topology(style: str = "full_mesh") -> dict:
    """修改拓扑模式。"""
    _current_topology["style"] = style
    return _build_topology(style)
