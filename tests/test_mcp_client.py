import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.mcp import MCPClient, MCPServerConfig


class FakeMCPHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length))
        if body["method"] == "tools/list":
            result = {
                "tools": [
                    {
                        "name": "demo_echo",
                        "description": "Echo text",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"text": {"type": "string"}},
                        },
                    }
                ]
            }
        else:
            result = {"content": [{"type": "text", "text": body["params"]["arguments"]["text"]}]}
        payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format, *args):
        return


class MCPClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.server = HTTPServer(("127.0.0.1", 0), FakeMCPHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.config = MCPServerConfig(
            server_id="fake",
            name="Fake",
            enabled=True,
            endpoint=f"http://127.0.0.1:{self.server.server_port}/mcp",
            allowed_tools=["*"],
        )

    async def asyncTearDown(self):
        self.server.shutdown()
        self.thread.join(timeout=2)

    async def test_list_tools_and_call_tool(self):
        client = MCPClient()
        tools = await client.list_tools(self.config)
        self.assertEqual(tools[0].tool_name, "demo_echo")
        result = await client.call_tool(self.config, "demo_echo", {"text": "hi"})
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["content"][0]["text"], "hi")


if __name__ == "__main__":
    unittest.main()
