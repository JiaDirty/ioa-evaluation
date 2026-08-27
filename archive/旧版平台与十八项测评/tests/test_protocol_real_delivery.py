import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from src.core.data_models import ProtocolMessage, ProtocolType
from src.protocol.adapters import A2AAdapter, MCPAdapter, PrivateAPIAdapter, ProtocolDeliveryError


class _CaptureHandler(BaseHTTPRequestHandler):
    received = {}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode("utf-8")
        _CaptureHandler.received = {
            "path": self.path,
            "protocol": self.headers.get("X-IoA-Protocol"),
            "body": json.loads(body),
        }
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok": true}')

    def log_message(self, format, *args):
        return


class ProtocolRealDeliveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_requires_real_endpoint(self):
        adapter = A2AAdapter()
        msg = ProtocolMessage(
            source_protocol=ProtocolType.A2A,
            target_protocol=ProtocolType.A2A,
            source_agent_id="a",
            target_agent_id="b",
            method="execute_task",
        )

        with self.assertRaises(ProtocolDeliveryError):
            await adapter.send_message("", msg)

    async def test_send_message_posts_to_real_http_endpoint(self):
        server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/a2a"
            adapter = A2AAdapter()
            msg = ProtocolMessage(
                source_protocol=ProtocolType.A2A,
                target_protocol=ProtocolType.A2A,
                source_agent_id="a",
                target_agent_id="b",
                method="execute_task",
                params={"task": "hello"},
            )

            result = await adapter.send_message(endpoint, msg)

            self.assertEqual(result["status"], "delivered")
            self.assertEqual(_CaptureHandler.received["path"], "/a2a")
            self.assertEqual(_CaptureHandler.received["protocol"], "a2a")
            self.assertEqual(_CaptureHandler.received["body"]["method"], "SendMessage")
            self.assertEqual(
                _CaptureHandler.received["body"]["params"]["message"]["parts"][0]["text"],
                "hello",
            )
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

    async def test_all_supported_protocols_post_real_http_messages(self):
        server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            endpoint = f"http://127.0.0.1:{server.server_port}/protocol"
            cases = [
                (A2AAdapter(), ProtocolType.A2A, "a2a"),
                (MCPAdapter(), ProtocolType.MCP, "mcp"),
                (PrivateAPIAdapter(), ProtocolType.PRIVATE_API, "private_api"),
            ]
            for adapter, protocol, header in cases:
                msg = ProtocolMessage(
                    source_protocol=protocol,
                    target_protocol=protocol,
                    source_agent_id="a",
                    target_agent_id="b",
                    method="execute_task",
                    params={"task": "hello"},
                )

                result = await adapter.send_message(endpoint, msg)

                self.assertEqual(result["status"], "delivered")
                self.assertEqual(_CaptureHandler.received["protocol"], header)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
