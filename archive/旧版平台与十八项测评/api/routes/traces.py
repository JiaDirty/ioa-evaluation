"""Trace/event query API."""

from __future__ import annotations

from fastapi import APIRouter

from api.state import get_ioa_env

router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("/{trace_id}/events")
async def get_trace_events(trace_id: str) -> list[dict]:
    env = await get_ioa_env()
    return [event.model_dump(mode="json") for event in env.event_bus.query(trace_id=trace_id)]
