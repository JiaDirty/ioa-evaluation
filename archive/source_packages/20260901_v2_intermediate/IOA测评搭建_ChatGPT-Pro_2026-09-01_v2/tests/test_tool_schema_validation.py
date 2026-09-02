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

    async def test_gateway_validates_strict_nested_schema_constraints(self):
        registry = ToolRegistry()
        registry.register(
            ToolDescriptor(
                tool_id="plan",
                name="Plan",
                input_schema={
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["status", "items"],
                    "properties": {
                        "status": {"type": "string", "enum": ["OPEN", "CLOSED"]},
                        "items": {
                            "type": "array",
                            "minItems": 1,
                            "items": {
                                "type": "object",
                                "additionalProperties": False,
                                "required": ["id"],
                                "properties": {"id": {"type": "string"}},
                            },
                        },
                    },
                },
            ),
            lambda status, items: {"status": status, "items": items},
        )
        gateway = ToolGateway(registry)

        invalid_enum = await gateway.call_tool(ToolCall(
            tool_id="plan", arguments={"status": "UNKNOWN", "items": [{"id": "A"}]}
        ))
        empty_items = await gateway.call_tool(ToolCall(
            tool_id="plan", arguments={"status": "OPEN", "items": []}
        ))
        missing_nested = await gateway.call_tool(ToolCall(
            tool_id="plan", arguments={"status": "OPEN", "items": [{}]}
        ))
        extra = await gateway.call_tool(ToolCall(
            tool_id="plan", arguments={"status": "OPEN", "items": [{"id": "A"}], "extra": 1}
        ))

        self.assertEqual(invalid_enum.status, "failed")
        self.assertEqual(empty_items.status, "failed")
        self.assertEqual(missing_nested.status, "failed")
        self.assertEqual(extra.status, "failed")


if __name__ == "__main__":
    unittest.main()
