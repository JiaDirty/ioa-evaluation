import unittest

from src.tools import build_default_tool_gateway, load_tool_descriptors


class ToolsConfigTest(unittest.TestCase):
    def test_loads_yaml_tool_descriptors(self):
        descriptors = load_tool_descriptors("config/tools.yaml")
        ids = {descriptor.tool_id for descriptor in descriptors}
        self.assertIn("get_stock_price", ids)
        self.assertIn("fact_check", ids)
        self.assertTrue(all(descriptor.provider == "local" for descriptor in descriptors))

    def test_default_gateway_uses_config_and_falls_back_for_missing_file(self):
        gateway = build_default_tool_gateway()
        configured = gateway.get_tool("get_stock_price")
        self.assertIsNotNone(configured)
        self.assertEqual(configured["name"], "Stock Price Lookup")

        fallback = build_default_tool_gateway("missing-tools.yaml")
        self.assertIsNotNone(fallback.get_tool("get_stock_price"))


if __name__ == "__main__":
    unittest.main()
