"""Agent 拓扑 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import get_ioa_env
from src.core.data_models import AgentCard, AgentStatus

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


@router.get("/registry")
async def list_agent_registry(
    sub_ioa_id: str | None = None,
    include_inactive: bool = True,
) -> list[dict]:
    """List AgentCards in the global registry for registry UI inspection."""
    env = await get_ioa_env()
    cards = await env.global_registry.list_agents(
        sub_ioa_id=sub_ioa_id,
        include_inactive=include_inactive,
    )
    return [card.model_dump(mode="json") for card in cards]


@router.put("/topology")
async def update_topology(style: str = "full_mesh") -> dict:
    """修改拓扑模式。"""
    _current_topology["style"] = style
    return _build_topology(style)


@router.post("/onboard")
async def onboard_agent(card_payload: dict) -> dict:
    """Register an AgentCard through the product onboarding flow."""
    env = await get_ioa_env()
    try:
        card = AgentCard(**card_payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not card.endpoint:
        raise HTTPException(status_code=400, detail="endpoint is required for onboarding")
    if not card.supported_protocols:
        raise HTTPException(status_code=400, detail="supported_protocols is required")
    if not card.permission_scope:
        raise HTTPException(status_code=400, detail="permission_scope is required")
    if card.sub_ioa_id not in env.get_sub_ioa_ids():
        raise HTTPException(status_code=404, detail=f"Unknown sub_ioa_id: {card.sub_ioa_id}")
    card.status = AgentStatus.SUSPENDED
    agent_id = await env.register_agent(card)
    report = _verification_report(card)
    return {
        "agent_id": agent_id,
        "status": card.status.value,
        "onboarding_report": report,
    }


@router.post("/{agent_id}/verify")
async def verify_agent(agent_id: str) -> dict:
    env = await get_ioa_env()
    card = await env.global_registry.get_agent(agent_id)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    result = await env.global_registry.verify_identity(agent_id)
    report = _verification_report(card)
    report["certificate_valid"] = result.certificate_valid
    report["findings"].append(result.reason) if result.reason else None
    return report


@router.post("/{agent_id}/activate")
async def activate_agent(agent_id: str) -> dict:
    card = await _update_agent_status(agent_id, AgentStatus.ACTIVE)
    return {"agent_id": agent_id, "status": card.status.value}


@router.post("/{agent_id}/suspend")
async def suspend_agent(agent_id: str) -> dict:
    card = await _update_agent_status(agent_id, AgentStatus.SUSPENDED)
    return {"agent_id": agent_id, "status": card.status.value}


@router.get("/{agent_id}/card")
async def get_agent_card(agent_id: str) -> dict:
    env = await get_ioa_env()
    card = await env.global_registry.get_agent(agent_id)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return card.model_dump(mode="json")


@router.get("/{agent_id}/verification-report")
async def get_verification_report(agent_id: str) -> dict:
    return await verify_agent(agent_id)


async def _update_agent_status(agent_id: str, status: AgentStatus) -> AgentCard:
    env = await get_ioa_env()
    card = await env.global_registry.get_agent(agent_id)
    if not card:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    await env.global_registry.update_agent_status(agent_id, status)
    local = env.get_local_registry(card.sub_ioa_id)
    if local:
        await local.update_agent_status(agent_id, status)
    updated = await env.global_registry.get_agent(agent_id)
    if not updated:
        raise HTTPException(status_code=404, detail=f"Agent not found: {agent_id}")
    return updated


def _verification_report(card: AgentCard) -> dict:
    endpoint_reachable = card.endpoint.startswith("http://") or card.endpoint.startswith("https://")
    findings = []
    if not endpoint_reachable:
        findings.append("endpoint is not an HTTP URL")
    if not card.declared_capabilities:
        findings.append("declared_capabilities is empty")
    return {
        "agent_id": card.agent_id,
        "schema_valid": True,
        "endpoint_reachable": endpoint_reachable,
        "certificate_valid": False,
        "capability_tests": [],
        "trust_level": "sandboxed",
        "findings": findings,
    }
