import unittest

from src.mcp import MCPServerConfig, MCPServerRegistry, MCPToolCallResult, MCPToolInfo, MCPToolProvider
from src.tools import ToolCall, ToolGateway, ToolRegistry


class FakeMCPClient:
    async def list_tools(self, server):
        return [MCPToolInfo(server_id=server.server_id, tool_name="danger")]

    async def call_tool(self, server, tool_name, arguments):
        return MCPToolCallResult(
            server_id=server.server_id,
            tool_name=tool_name,
            status="completed",
            output={"ok": True},
        )


class MCPPolicyEnforcementTest(unittest.IsolatedAsyncioTestCase):
    async def test_empty_allowlist_denies_and_high_risk_requires_scope(self):
        registry = MCPServerRegistry()
        registry.register(MCPServerConfig(server_id="empty", name="Empty", enabled=True, allowed_tools=[]))
        tools = ToolRegistry()
        provider = MCPToolProvider(registry, FakeMCPClient())
        self.assertEqual(await provider.sync_tools(tools), 0)

        registry.register(
            MCPServerConfig(
                server_id="risk",
                name="Risk",
                enabled=True,
                allowed_tools=["danger"],
                risk_level="high",
            )
        )
        self.assertEqual(await provider.sync_tools(tools, server_id="risk"), 1)
        gateway = ToolGateway(tools)
        gateway.register_provider("mcp", provider)
        denied = await gateway.call_tool(ToolCall(tool_id="mcp:risk:danger"))
        self.assertEqual(denied.status, "denied")
        ok = await gateway.call_tool(ToolCall(tool_id="mcp:risk:danger", granted_scopes=["high_risk_tool"]))
        self.assertEqual(ok.status, "completed")


if __name__ == "__main__":
    unittest.main()
