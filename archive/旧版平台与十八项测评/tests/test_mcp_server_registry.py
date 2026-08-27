import tempfile
import unittest
from pathlib import Path

from src.mcp import MCPServerConfig, MCPServerRegistry


class MCPServerRegistryTest(unittest.TestCase):
    def test_missing_yaml_returns_empty_registry(self):
        registry = MCPServerRegistry.from_yaml("missing-mcp-config.yaml")
        self.assertEqual(registry.list_servers(), [])

    def test_loads_yaml_and_filters_enabled_servers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "mcp_servers.yaml"
            path.write_text(
                """
servers:
  - server_id: disabled
    name: Disabled
    enabled: false
    transport: http
  - server_id: enabled
    name: Enabled
    enabled: true
    transport: http
    endpoint: http://127.0.0.1:9100/mcp
    allowed_tools: ["*"]
""",
                encoding="utf-8",
            )
            registry = MCPServerRegistry.from_yaml(path)
        self.assertEqual([server.server_id for server in registry.list_servers()], ["disabled", "enabled"])
        self.assertEqual([server.server_id for server in registry.list_enabled_servers()], ["enabled"])

    def test_later_register_overwrites_duplicate_server_id(self):
        registry = MCPServerRegistry()
        registry.register(MCPServerConfig(server_id="s1", name="first"))
        registry.register(MCPServerConfig(server_id="s1", name="second", enabled=True))
        self.assertEqual(registry.get("s1").name, "second")
        self.assertTrue(registry.get("s1").enabled)


if __name__ == "__main__":
    unittest.main()
