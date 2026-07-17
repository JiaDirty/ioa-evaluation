"""Protocol routing boundary."""

from __future__ import annotations

from typing import Any

from ..core.data_models import ProtocolMessage, ProtocolType
from .adapters import ProtocolDeliveryError, create_adapter


class ProtocolRouter:
    async def route_agent_call(
        self,
        endpoint: str,
        protocol: ProtocolType,
        message: ProtocolMessage,
    ) -> dict[str, Any]:
        if protocol == ProtocolType.MCP:
            raise ProtocolDeliveryError("MCP cannot be used for agent-to-agent calls")
        adapter = create_adapter(protocol)
        return await adapter.send_message(endpoint, message)

    async def route_tool_call(self, *_args, **_kwargs):
        raise NotImplementedError("Tool calls must go through ToolGateway, not ProtocolRouter")
