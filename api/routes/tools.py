"""Tool registry API."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from api.state import get_ioa_env
from src.tools.models import ToolCall

router = APIRouter(prefix="/api/tools", tags=["tools"])


@router.get("")
async def list_tools() -> list[dict]:
    env = await get_ioa_env()
    return env.tool_gateway.list_tools()


@router.get("/history")
async def tool_history() -> list[dict]:
    env = await get_ioa_env()
    if getattr(env, "tool_call_store", None) is not None:
        return env.tool_call_store.list_recent()
    return [result.model_dump(mode="json") for result in env.tool_gateway.history()]


@router.get("/{tool_id}")
async def get_tool(tool_id: str) -> dict:
    env = await get_ioa_env()
    descriptor = env.tool_gateway.get_tool(tool_id)
    if descriptor is None:
        raise HTTPException(status_code=404, detail=f"Tool not found: {tool_id}")
    return descriptor


@router.post("/{tool_id}/call")
async def call_tool(tool_id: str, body: dict) -> dict:
    env = await get_ioa_env()
    call = ToolCall(
        tool_id=tool_id,
        task_id=str(body.get("task_id", "")),
        trace_id=str(body.get("trace_id", "")),
        caller_agent_id=str(body.get("caller_agent_id", "api-user")),
        arguments=dict(body.get("arguments", {})),
        granted_scopes=list(body.get("granted_scopes", [])),
    )
    result = await env.tool_gateway.call_tool(call)
    return result.model_dump(mode="json")
