"""MCP server registry and tool sync API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import get_ioa_env

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.get("/servers")
async def list_mcp_servers() -> list[dict]:
    env = await get_ioa_env()
    return [
        server.model_dump(mode="json")
        for server in env.mcp_server_registry.list_servers(include_disabled=True)
    ]


@router.post("/servers/{server_id}/sync-tools")
async def sync_mcp_server_tools(server_id: str) -> dict:
    env = await get_ioa_env()
    try:
        synced = await env.mcp_tool_provider.sync_tools(env.tool_gateway.registry, server_id=server_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"MCP server not found: {server_id}") from None
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    return {"server_id": server_id, "synced": synced}


@router.post("/sync-tools")
async def sync_all_mcp_tools() -> dict:
    env = await get_ioa_env()
    synced = await env.mcp_tool_provider.sync_tools(env.tool_gateway.registry)
    return {"synced": synced}
