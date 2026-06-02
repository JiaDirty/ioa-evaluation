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

from ..core.data_models import AgentCard, ProtocolType
from ..attacks.observation import NetworkObservationEvent
from .adapters import create_adapter
from .a2a_official import (
    A2A_ERROR_SPECS,
    A2A_METHOD_CANCEL_TASK,
    A2A_METHOD_GET_TASK,
    A2A_METHOD_SEND_MESSAGE,
    A2A_METHOD_GET_EXTENDED_AGENT_CARD,
    A2A_METHOD_SEND_STREAMING_MESSAGE,
    A2A_METHOD_SUBSCRIBE_TO_TASK,
    A2A_PROTOCOL_VERSION,
    A2A_PUSH_NOTIFICATION_METHODS,
    build_http_error,
    build_agent_card,
    build_jsonrpc_error,
    build_send_message_response,
    build_task,
    build_task_response,
    decode_send_message_request,
    is_terminal_task,
)

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
        self._agent_cards: dict[str, AgentCard] = {}
        self._a2a_tasks: dict[str, dict] = {}
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

    def agent_card_url_for(self, agent_id: str) -> str:
        return f"{self.endpoint_for(agent_id)}/.well-known/agent-card.json"

    def register_agent_card(self, card: AgentCard) -> None:
        self._agent_cards[card.agent_id] = card

    def _make_handler(self):
        outer = self

        class AgentEndpointHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                try:
                    agent_id = self._agent_id_from_well_known_path()
                    if agent_id:
                        card = outer._agent_cards.get(agent_id)
                        if not card:
                            self._send_json(404, {"error": f"unknown agent card: {agent_id}"})
                            return
                        self._send_json(200, build_agent_card(card), content_type="application/a2a+json")
                        return

                    task_id = self._task_id_from_rest_path()
                    if task_id:
                        if not self._validate_a2a_version(is_jsonrpc=False):
                            return
                        task = outer._a2a_tasks.get(task_id)
                        if not task:
                            self._send_a2a_http_error(
                                "TaskNotFoundError",
                                f"Task not found: {task_id}",
                                {"taskId": task_id},
                            )
                            return
                        self._send_json(200, task, content_type="application/a2a+json")
                        return

                    self._send_json(404, {"error": "unknown A2A endpoint path"})
                except Exception as exc:
                    logger.exception("Local agent card endpoint failed")
                    self._send_json(500, {"error": str(exc)})

            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length).decode("utf-8")
                    protocol = self.headers.get("X-IoA-Protocol", "a2a")
                    raw_data = json.loads(raw) if raw else {}
                    is_jsonrpc = raw_data.get("jsonrpc") == "2.0"
                    if not self._validate_a2a_version(is_jsonrpc=is_jsonrpc, request=raw_data):
                        return

                    method = raw_data.get("method")
                    if protocol == "a2a" and method in {
                        A2A_METHOD_SEND_STREAMING_MESSAGE,
                        A2A_METHOD_SUBSCRIBE_TO_TASK,
                        A2A_METHOD_GET_EXTENDED_AGENT_CARD,
                    }:
                        self._send_a2a_jsonrpc_error(
                            raw_data,
                            "UnsupportedOperationError",
                            f"A2A method not supported by this testbed endpoint: {method}",
                        )
                        return
                    if protocol == "a2a" and method in A2A_PUSH_NOTIFICATION_METHODS:
                        self._send_a2a_jsonrpc_error(
                            raw_data,
                            "PushNotificationNotSupportedError",
                            "Push notifications are not supported by this testbed endpoint",
                        )
                        return

                    rest_agent_id = self._agent_id_from_rest_send_path()
                    if rest_agent_id:
                        self._handle_a2a_rest_send(raw_data, rest_agent_id)
                        return

                    rest_cancel_task_id = self._task_id_from_rest_path(cancel=True)
                    if rest_cancel_task_id:
                        self._handle_a2a_rest_cancel(rest_cancel_task_id)
                        return

                    if protocol == "a2a" and raw_data.get("method") in {
                        A2A_METHOD_GET_TASK,
                        A2A_METHOD_CANCEL_TASK,
                    }:
                        self._handle_a2a_task_control(raw_data)
                        return
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
                    if protocol == "a2a" and raw_data.get("method") == A2A_METHOD_SEND_MESSAGE:
                        response_payload = build_task_response(
                            request_id=str(raw_data.get("id", message.message_id)),
                            protocol_message=message,
                            content=content,
                            source_agent_id=agent_id,
                            source_sub_ioa_id=sub_ioa_id,
                        )
                        task = response_payload["result"]["task"]
                        outer._a2a_tasks[task["id"]] = task
                        self._send_json(200, response_payload)
                    else:
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
                if len(path) == 3 and path[0] == "agents" and path[2] == "message:send":
                    return path[1]
                return ""

            def _agent_id_from_rest_send_path(self) -> str:
                path = urlparse(self.path).path.strip("/").split("/")
                if len(path) == 3 and path[0] == "agents" and path[2] == "message:send":
                    return path[1]
                return ""

            def _agent_id_from_well_known_path(self) -> str:
                path = urlparse(self.path).path.strip("/").split("/")
                if (
                    len(path) == 4
                    and path[0] == "agents"
                    and path[2] == ".well-known"
                    and path[3] == "agent-card.json"
                ):
                    return path[1]
                return ""

            def _task_id_from_rest_path(self, cancel: bool = False) -> str:
                path = urlparse(self.path).path.strip("/").split("/")
                if len(path) != 4 or path[0] != "agents" or path[2] != "tasks":
                    return ""
                task_id = path[3]
                if cancel:
                    return task_id[:-7] if task_id.endswith(":cancel") else ""
                return "" if task_id.endswith(":cancel") else task_id

            def _handle_a2a_rest_send(self, request: dict, agent_id: str) -> None:
                message = decode_send_message_request(request, request_id=request.get("message", {}).get("messageId", ""))
                if not message.target_agent_id:
                    message.target_agent_id = agent_id
                sub_ioa_id = outer.sub_ioa_lookup(agent_id)
                if not sub_ioa_id:
                    self._send_a2a_http_error(
                        "TaskNotFoundError",
                        f"unknown agent endpoint: {agent_id}",
                        {"agentId": agent_id},
                    )
                    return
                prompt = self._build_prompt("a2a", message)
                self._record_observation(message, sub_ioa_id)
                content = outer.runner(sub_ioa_id, agent_id, prompt)
                response_payload = build_send_message_response(
                    protocol_message=message,
                    content=content,
                    source_agent_id=agent_id,
                    source_sub_ioa_id=sub_ioa_id,
                )
                task = response_payload["task"]
                outer._a2a_tasks[task["id"]] = task
                self._send_json(200, response_payload, content_type="application/a2a+json")

            def _handle_a2a_rest_cancel(self, task_id: str) -> None:
                task = outer._a2a_tasks.get(task_id)
                if not task:
                    self._send_a2a_http_error(
                        "TaskNotFoundError",
                        f"Task not found: {task_id}",
                        {"taskId": task_id},
                    )
                    return
                if is_terminal_task(task):
                    self._send_a2a_http_error(
                        "TaskNotCancelableError",
                        f"Task is not cancelable: {task_id}",
                        {"taskId": task_id},
                    )
                    return
                canceled = build_task(
                    task_id=task_id,
                    context_id=task.get("contextId", ""),
                    content="Task cancellation requested.",
                    source_agent_id=task.get("metadata", {}).get("sourceAgentId", ""),
                    source_sub_ioa_id=task.get("metadata", {}).get("sourceSubIoaId", ""),
                    state="TASK_STATE_CANCELED",
                )
                outer._a2a_tasks[task_id] = canceled
                self._send_json(200, canceled, content_type="application/a2a+json")

            def _handle_a2a_task_control(self, request: dict) -> None:
                method = request.get("method")
                params = request.get("params", {}) if isinstance(request.get("params"), dict) else {}
                task_id = str(params.get("id", ""))
                task = outer._a2a_tasks.get(task_id)
                if not task:
                    self._send_a2a_jsonrpc_error(
                        request,
                        "TaskNotFoundError",
                        f"Task not found: {task_id}",
                        {"taskId": task_id},
                    )
                    return
                if method == A2A_METHOD_CANCEL_TASK:
                    if is_terminal_task(task):
                        self._send_a2a_jsonrpc_error(
                            request,
                            "TaskNotCancelableError",
                            f"Task is not cancelable: {task_id}",
                            {"taskId": task_id},
                        )
                        return
                    task = build_task(
                        task_id=task.get("id", task_id),
                        context_id=task.get("contextId", ""),
                        content="Task cancellation requested.",
                        source_agent_id=task.get("metadata", {}).get("sourceAgentId", ""),
                        source_sub_ioa_id=task.get("metadata", {}).get("sourceSubIoaId", ""),
                        state="TASK_STATE_CANCELED",
                    )
                    outer._a2a_tasks[task_id] = task
                self._send_json(200, {
                    "jsonrpc": "2.0",
                    "id": request.get("id"),
                    "result": task,
                })

            def _validate_a2a_version(
                self,
                is_jsonrpc: bool,
                request: dict | None = None,
            ) -> bool:
                version = self.headers.get("A2A-Version", A2A_PROTOCOL_VERSION)
                if version == A2A_PROTOCOL_VERSION:
                    return True
                message = f"A2A version not supported: {version}"
                metadata = {"requestedVersion": version, "supportedVersion": A2A_PROTOCOL_VERSION}
                if is_jsonrpc:
                    self._send_a2a_jsonrpc_error(
                        request or {},
                        "VersionNotSupportedError",
                        message,
                        metadata,
                    )
                else:
                    self._send_a2a_http_error("VersionNotSupportedError", message, metadata)
                return False

            def _send_a2a_jsonrpc_error(
                self,
                request: dict,
                error_type: str,
                message: str,
                metadata: dict | None = None,
            ) -> None:
                _rpc_code, http_status, _status, _reason = A2A_ERROR_SPECS[error_type]
                payload = build_jsonrpc_error(
                    request_id=request.get("id"),
                    error_type=error_type,
                    message=message,
                    metadata=metadata,
                )
                self._send_json(http_status, payload)

            def _send_a2a_http_error(
                self,
                error_type: str,
                message: str,
                metadata: dict | None = None,
            ) -> None:
                status, payload = build_http_error(error_type, message, metadata)
                self._send_json(status, payload, content_type="application/a2a+json")

            def _record_observation(self, message, sub_ioa_id: str) -> None:
                if outer.observation_sink is None:
                    return
                outer.observation_sink(NetworkObservationEvent(
                    timestamp=datetime.now(),
                    trace_id=message.trace_id,
                    source_domain=self._source_domain_hint(message.source_agent_id),
                    target_domain_hint=sub_ioa_id,
                    protocol="a2a",
                    status_hint="observed",
                ))

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

            def _send_json(
                self,
                status: int,
                payload: dict,
                content_type: str = "application/json",
            ) -> None:
                raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", f"{content_type}; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, format, *args):
                return

        return AgentEndpointHandler
