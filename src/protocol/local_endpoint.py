"""Local HTTP endpoints for live IoA agent runtimes.

The benchmark can run on one machine, but Gateway delivery should still cross
a protocol boundary. This server exposes each registered AgentCard as an HTTP
endpoint and delegates the request to the real AG2/LLM runtime.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse

from ..core.data_models import ProtocolType
from ..attacks.observation import NetworkObservationEvent
from .adapters import create_adapter

logger = logging.getLogger(__name__)


class LocalAgentEndpointServer:
    """HTTP transport boundary for local live AgentCard runtimes."""

    def __init__(
        self,
        runner: Callable[[str, str, str], str],
        sub_ioa_lookup: Callable[[str], str | None],
        observation_sink: Callable[[NetworkObservationEvent], None] | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        self.runner = runner
        self.sub_ioa_lookup = sub_ioa_lookup
        self.observation_sink = observation_sink
        self.host = host
        self._server = ThreadingHTTPServer((host, port), self._make_handler())
        self.port = int(self._server.server_address[1])
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="ioa-local-agent-endpoint",
            daemon=True,
        )
        self._thread.start()
        logger.info("Local agent endpoint server listening on %s:%s", self.host, self.port)

    def stop(self) -> None:
        self._server.shutdown()
        if self._thread:
            self._thread.join(timeout=3)
        self._server.server_close()

    def endpoint_for(self, agent_id: str) -> str:
        return f"http://{self.host}:{self.port}/agents/{agent_id}"

    def _make_handler(self):
        outer = self

        class AgentEndpointHandler(BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length).decode("utf-8")
                    protocol = self.headers.get("X-IoA-Protocol", "a2a")
                    message = create_adapter(ProtocolType(protocol)).decode(raw)
                    agent_id = message.target_agent_id or self._agent_id_from_path()
                    if not agent_id:
                        self._send_json(400, {"status": "failed", "error": "missing target agent id"})
                        return

                    sub_ioa_id = outer.sub_ioa_lookup(agent_id)
                    if not sub_ioa_id:
                        self._send_json(
                            404,
                            {"status": "failed", "error": f"unknown agent endpoint: {agent_id}"},
                        )
                        return

                    prompt = self._build_prompt(protocol, message)
                    if outer.observation_sink is not None:
                        outer.observation_sink(NetworkObservationEvent(
                            timestamp=datetime.now(),
                            trace_id=message.trace_id,
                            source_domain=self._source_domain_hint(message.source_agent_id),
                            target_domain_hint=sub_ioa_id,
                            protocol=protocol,
                            status_hint="observed",
                        ))
                    content = outer.runner(sub_ioa_id, agent_id, prompt)
                    self._send_json(200, {
                        "status": "completed",
                        "content": content,
                        "source_agent_id": agent_id,
                        "source_sub_ioa_id": sub_ioa_id,
                        "trace_id": message.trace_id,
                        "message_id": message.message_id,
                    })
                except Exception as exc:
                    logger.exception("Local agent endpoint failed")
                    self._send_json(500, {"status": "failed", "error": str(exc)})

            def _agent_id_from_path(self) -> str:
                path = urlparse(self.path).path.strip("/").split("/")
                if len(path) == 2 and path[0] == "agents":
                    return path[1]
                return ""

            @staticmethod
            def _source_domain_hint(source_agent_id: str) -> str:
                if source_agent_id.endswith("-gw"):
                    return source_agent_id[:-3]
                if "-" in source_agent_id:
                    return source_agent_id.split("-", 1)[0]
                return "unknown"

            @staticmethod
            def _build_prompt(protocol: str, message) -> str:
                task = message.params.get("task", "")
                payload = message.params.get("payload", {})
                prompt = (
                    f"[Protocol: {protocol}]\n"
                    f"[Task ID: {message.trace_id}]\n"
                    f"[Message ID: {message.message_id}]\n\n"
                    f"{task}"
                )
                if payload:
                    prompt += "\n\nPayload:\n"
                    prompt += json.dumps(payload, ensure_ascii=False, default=str)
                return prompt

            def _send_json(self, status: int, payload: dict) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format, *args):
                return

        return AgentEndpointHandler
