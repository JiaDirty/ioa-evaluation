import unittest

from src.tools import ToolCall, ToolDescriptor, ToolGateway, ToolRegistry


class ToolSchemaValidationTest(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_validates_required_and_type(self):
        registry = ToolRegistry()
        registry.register(
            ToolDescriptor(
                tool_id="quote",
                name="Quote",
                input_schema={
                    "type": "object",
                    "required": ["ticker"],
                    "properties": {"ticker": {"type": "string"}},
                },
            ),
            lambda ticker: {"ticker": ticker},
        )
        gateway = ToolGateway(registry)
        missing = await gateway.call_tool(ToolCall(tool_id="quote", arguments={}))
        self.assertEqual(missing.status, "failed")
        self.assertIn("missing required argument", missing.error)

        wrong_type = await gateway.call_tool(ToolCall(tool_id="quote", arguments={"ticker": 123}))
        self.assertEqual(wrong_type.status, "failed")
        self.assertIn("must be string", wrong_type.error)

        ok = await gateway.call_tool(ToolCall(tool_id="quote", arguments={"ticker": "AAPL"}))
        self.assertEqual(ok.status, "completed")


if __name__ == "__main__":
    unittest.main()
