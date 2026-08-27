import unittest

from src.mcp import MCPServerConfig, MCPServerRegistry, MCPToolCallResult, MCPToolInfo, MCPToolProvider
from src.tools import ToolCall, ToolGateway, ToolRegistry


class FakeMCPClient:
    async def list_tools(self, server):
        return [
            MCPToolInfo(server_id=server.server_id, tool_name="demo_echo", description="Echo"),
            MCPToolInfo(server_id=server.server_id, tool_name="not_allowed", description="Blocked"),
        ]

    async def call_tool(self, server, tool_name, arguments):
        return MCPToolCallResult(
            server_id=server.server_id,
            tool_name=tool_name,
            status="completed",
            output={"echo": arguments},
        )


class MCPToolProviderTest(unittest.IsolatedAsyncioTestCase):
    async def test_sync_skips_disabled_and_allowlist_blocks_unlisted_tools(self):
        registry = MCPServerRegistry()
        registry.register(MCPServerConfig(server_id="disabled", name="Disabled", enabled=False, allowed_tools=["*"]))
        registry.register(MCPServerConfig(server_id="enabled", name="Enabled", enabled=True, allowed_tools=["demo_echo"]))
        tools = ToolRegistry()
        provider = MCPToolProvider(registry, FakeMCPClient())
        synced = await provider.sync_tools(tools)
        self.assertEqual(synced, 1)
        self.assertIsNotNone(tools.get("mcp:enabled:demo_echo"))
        self.assertIsNone(tools.get("mcp:enabled:not_allowed"))

    async def test_gateway_calls_mcp_provider(self):
        registry = MCPServerRegistry()
        registry.register(MCPServerConfig(server_id="enabled", name="Enabled", enabled=True, allowed_tools=["demo_echo"]))
        tools = ToolRegistry()
        provider = MCPToolProvider(registry, FakeMCPClient())
        await provider.sync_tools(tools)
        gateway = ToolGateway(tools)
        gateway.register_provider("mcp", provider)

        result = await gateway.call_tool(ToolCall(tool_id="mcp:enabled:demo_echo", arguments={"text": "hi"}))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["echo"]["text"], "hi")

    async def test_empty_allowlist_denies_registration(self):
        registry = MCPServerRegistry()
        registry.register(MCPServerConfig(server_id="enabled", name="Enabled", enabled=True, allowed_tools=[]))
        tools = ToolRegistry()
        synced = await MCPToolProvider(registry, FakeMCPClient()).sync_tools(tools)
        self.assertEqual(synced, 0)


if __name__ == "__main__":
    unittest.main()
