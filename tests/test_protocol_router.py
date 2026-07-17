import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.core.data_models import ProtocolMessage, ProtocolType
from src.protocol.adapters import ProtocolDeliveryError
from src.protocol.router import ProtocolRouter


class ProtocolRouterTest(unittest.IsolatedAsyncioTestCase):
    def _message(self, protocol: ProtocolType) -> ProtocolMessage:
        return ProtocolMessage(
            source_protocol=protocol,
            target_protocol=protocol,
            source_agent_id="gw",
            target_agent_id="agent",
            method="execute_task",
        )

    async def test_agent_call_routes_a2a_and_private_api(self):
        fake_adapter = Mock()
        fake_adapter.send_message = AsyncMock(return_value={"status": "ok"})
        with patch("src.protocol.router.create_adapter", return_value=fake_adapter):
            self.assertEqual(await ProtocolRouter().route_agent_call("http://x", ProtocolType.A2A, self._message(ProtocolType.A2A)), {"status": "ok"})
            self.assertEqual(await ProtocolRouter().route_agent_call("http://x", ProtocolType.PRIVATE_API, self._message(ProtocolType.PRIVATE_API)), {"status": "ok"})

    async def test_mcp_cannot_route_agent_call(self):
        with self.assertRaises(ProtocolDeliveryError):
            await ProtocolRouter().route_agent_call("http://x", ProtocolType.MCP, self._message(ProtocolType.MCP))

    async def test_tool_call_boundary(self):
        with self.assertRaises(NotImplementedError):
            await ProtocolRouter().route_tool_call()


if __name__ == "__main__":
    unittest.main()
