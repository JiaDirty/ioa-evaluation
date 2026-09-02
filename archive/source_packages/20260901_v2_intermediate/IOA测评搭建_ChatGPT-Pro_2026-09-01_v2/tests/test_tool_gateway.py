import unittest

from src.tools import ToolCall, ToolDescriptor, ToolGateway, ToolRegistry


class ToolGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_local_tool_call_returns_structured_result(self):
        registry = ToolRegistry()
        registry.register(
            ToolDescriptor(tool_id="echo", name="Echo", required_scopes=["read"]),
            lambda text: {"text": text},
        )
        gateway = ToolGateway(registry)
        result = await gateway.call_tool(ToolCall(tool_id="echo", arguments={"text": "hi"}, granted_scopes=["read"]))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["text"], "hi")

    async def test_missing_scope_denies_tool_call(self):
        registry = ToolRegistry()
        registry.register(ToolDescriptor(tool_id="write", name="Write", required_scopes=["write"]))
        result = await ToolGateway(registry).call_tool(ToolCall(tool_id="write", granted_scopes=["read"]))
        self.assertEqual(result.status, "denied")

    async def test_high_risk_requires_high_risk_scope(self):
        registry = ToolRegistry()
        registry.register(ToolDescriptor(tool_id="trade", name="Trade", risk_level="high"))
        result = await ToolGateway(registry).call_tool(ToolCall(tool_id="trade", granted_scopes=["execute"]))
        self.assertEqual(result.status, "denied")


if __name__ == "__main__":
    unittest.main()
