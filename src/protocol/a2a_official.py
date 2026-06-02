"""Official A2A v1 JSON helpers used by the IoA protocol boundary."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from ..core.data_models import AgentCard, ProtocolMessage, ProtocolType

A2A_PROTOCOL_VERSION = "1.0"
A2A_BINDING_JSONRPC = "JSONRPC"
A2A_METHOD_SEND_MESSAGE = "SendMessage"
A2A_METHOD_GET_TASK = "GetTask"
A2A_METHOD_CANCEL_TASK = "CancelTask"
A2A_METHOD_SEND_STREAMING_MESSAGE = "SendStreamingMessage"
A2A_METHOD_SUBSCRIBE_TO_TASK = "SubscribeToTask"
A2A_METHOD_GET_EXTENDED_AGENT_CARD = "GetExtendedAgentCard"

A2A_PUSH_NOTIFICATION_METHODS = {
    "CreateTaskPushNotificationConfig",
    "GetTaskPushNotificationConfig",
    "ListTaskPushNotificationConfigs",
    "DeleteTaskPushNotificationConfig",
}

A2A_ERROR_SPECS = {
    "TaskNotFoundError": (-32001, 404, "NOT_FOUND", "TASK_NOT_FOUND"),
    "TaskNotCancelableError": (-32002, 400, "FAILED_PRECONDITION", "TASK_NOT_CANCELABLE"),
    "PushNotificationNotSupportedError": (
        -32003,
        400,
        "FAILED_PRECONDITION",
        "PUSH_NOTIFICATION_NOT_SUPPORTED",
    ),
    "UnsupportedOperationError": (-32004, 400, "FAILED_PRECONDITION", "UNSUPPORTED_OPERATION"),
    "ContentTypeNotSupportedError": (-32005, 400, "INVALID_ARGUMENT", "CONTENT_TYPE_NOT_SUPPORTED"),
    "InvalidAgentResponseError": (-32006, 500, "INTERNAL", "INVALID_AGENT_RESPONSE"),
    "ExtendedAgentCardNotConfiguredError": (
        -32007,
        400,
        "FAILED_PRECONDITION",
        "EXTENDED_AGENT_CARD_NOT_CONFIGURED",
    ),
    "ExtensionSupportRequiredError": (
        -32008,
        400,
        "FAILED_PRECONDITION",
        "EXTENSION_SUPPORT_REQUIRED",
    ),
    "VersionNotSupportedError": (-32009, 400, "FAILED_PRECONDITION", "VERSION_NOT_SUPPORTED"),
}


def build_jsonrpc_request(message: ProtocolMessage) -> dict[str, Any]:
    method = _official_method(message.method)
    params = _build_params(method, message)
    return {
        "jsonrpc": "2.0",
        "id": message.message_id,
        "method": method,
        "params": params,
    }


def decode_jsonrpc_request(data: dict[str, Any]) -> ProtocolMessage:
    method = data.get("method", "")
    if method == A2A_METHOD_SEND_MESSAGE:
        return _decode_send_message(data)

    params = data.get("params", {}) or {}
    metadata = params.get("metadata", {}) if isinstance(params, dict) else {}
    return ProtocolMessage(
        message_id=str(data.get("id", "")),
        source_protocol=ProtocolType.A2A,
        target_protocol=ProtocolType.A2A,
        source_agent_id=metadata.get("sourceAgentId", ""),
        target_agent_id=metadata.get("targetAgentId", ""),
        trace_id=metadata.get("traceId", ""),
        method=method,
        params=params if isinstance(params, dict) else {},
        metadata=metadata if isinstance(metadata, dict) else {},
    )


def decode_send_message_request(params: dict[str, Any], request_id: str = "") -> ProtocolMessage:
    return _decode_send_message({"id": request_id, "method": A2A_METHOD_SEND_MESSAGE, "params": params})


def build_task_response(
    request_id: str,
    protocol_message: ProtocolMessage,
    content: str,
    source_agent_id: str,
    source_sub_ioa_id: str,
) -> dict[str, Any]:
    task_id = protocol_message.trace_id or str(uuid.uuid4())
    context_id = protocol_message.metadata.get("contextId") or f"ctx-{task_id}"
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": {
            "task": build_task(
                task_id=task_id,
                context_id=context_id,
                content=content,
                source_agent_id=source_agent_id,
                source_sub_ioa_id=source_sub_ioa_id,
                message=protocol_message,
            )
        },
    }


def build_send_message_response(
    protocol_message: ProtocolMessage,
    content: str,
    source_agent_id: str,
    source_sub_ioa_id: str,
) -> dict[str, Any]:
    return {
        "task": build_task(
            task_id=protocol_message.trace_id or str(uuid.uuid4()),
            context_id=protocol_message.metadata.get("contextId") or f"ctx-{protocol_message.trace_id or uuid.uuid4()}",
            content=content,
            source_agent_id=source_agent_id,
            source_sub_ioa_id=source_sub_ioa_id,
            message=protocol_message,
        )
    }


def build_task(
    task_id: str,
    context_id: str,
    content: str,
    source_agent_id: str,
    source_sub_ioa_id: str,
    message: ProtocolMessage | None = None,
    state: str = "TASK_STATE_COMPLETED",
) -> dict[str, Any]:
    task = {
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": state,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "artifacts": [{
            "artifactId": str(uuid.uuid4()),
            "name": "IoA Agent Response",
            "parts": [{"text": content, "mediaType": "text/plain"}],
            "metadata": {
                "sourceAgentId": source_agent_id,
                "sourceSubIoaId": source_sub_ioa_id,
            },
        }],
        "metadata": {
            "sourceAgentId": source_agent_id,
            "sourceSubIoaId": source_sub_ioa_id,
        },
    }
    if message is not None:
        task["history"] = [build_a2a_message(message)]
    return task


def is_terminal_task(task: dict[str, Any]) -> bool:
    state = str(task.get("status", {}).get("state", ""))
    return state in {
        "TASK_STATE_COMPLETED",
        "TASK_STATE_FAILED",
        "TASK_STATE_CANCELED",
        "TASK_STATE_REJECTED",
    }


def build_jsonrpc_error(
    request_id: Any,
    error_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rpc_code, _http_status, _status, reason = A2A_ERROR_SPECS[error_type]
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {
            "code": rpc_code,
            "message": message,
            "data": [_error_info(reason, metadata)],
        },
    }


def build_http_error(
    error_type: str,
    message: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    _rpc_code, http_status, status, reason = A2A_ERROR_SPECS[error_type]
    return http_status, {
        "error": {
            "code": http_status,
            "status": status,
            "message": message,
            "details": [_error_info(reason, metadata)],
        }
    }


def normalize_response(parsed: dict[str, Any]) -> dict[str, Any]:
    if "error" in parsed:
        error = parsed.get("error") or {}
        return {"status": "failed", "error": error.get("message") or str(error)}

    result = parsed.get("result", parsed)
    if not isinstance(result, dict):
        return {"status": "completed", "content": result}

    task = result.get("task") or (result if "status" in result and "artifacts" in result else None)
    if isinstance(task, dict):
        state = str(task.get("status", {}).get("state", "TASK_STATE_COMPLETED"))
        metadata = task.get("metadata", {}) if isinstance(task.get("metadata"), dict) else {}
        source_agent_id = metadata.get("sourceAgentId", "")
        source_sub_ioa_id = metadata.get("sourceSubIoaId", "")
        artifact = _first(task.get("artifacts", []), {})
        if isinstance(artifact, dict):
            art_meta = artifact.get("metadata", {}) if isinstance(artifact.get("metadata"), dict) else {}
            source_agent_id = source_agent_id or art_meta.get("sourceAgentId", "")
            source_sub_ioa_id = source_sub_ioa_id or art_meta.get("sourceSubIoaId", "")
        content = _extract_artifact_text(task)
        return {
            "status": "completed" if state.endswith("_COMPLETED") else state.lower(),
            "content": content,
            "source_agent_id": source_agent_id,
            "source_sub_ioa_id": source_sub_ioa_id,
            "a2a_task_id": task.get("id", ""),
            "a2a_context_id": task.get("contextId", ""),
        }

    message = result.get("message")
    if isinstance(message, dict):
        return {
            "status": "completed",
            "content": _extract_parts_text(message.get("parts", [])),
        }

    return parsed


def build_agent_card(card: AgentCard) -> dict[str, Any]:
    endpoint = card.endpoint
    return {
        "name": card.display_name,
        "description": f"{card.display_name} in Sub-IoA {card.sub_ioa_id}",
        "supportedInterfaces": [{
            "url": endpoint,
            "protocolBinding": A2A_BINDING_JSONRPC,
            "protocolVersion": A2A_PROTOCOL_VERSION,
        }],
        "provider": {
            "organization": card.provider,
            "url": f"https://ioa.local/providers/{card.provider}",
        },
        "version": card.protocol_versions.get("a2a", A2A_PROTOCOL_VERSION),
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {},
        "securityRequirements": [],
        "defaultInputModes": ["text/plain", "application/json"],
        "defaultOutputModes": ["text/plain", "application/json"],
        "skills": [
            {
                "id": cap,
                "name": cap.replace("_", " ").title(),
                "description": f"Declared capability: {cap}",
                "tags": [card.sub_ioa_id, cap],
            }
            for cap in card.declared_capabilities
        ] or [{
            "id": "general",
            "name": "General Task Execution",
            "description": "Execute general delegated tasks.",
            "tags": [card.sub_ioa_id],
        }],
    }


def build_a2a_message(message: ProtocolMessage) -> dict[str, Any]:
    task_text = str(message.params.get("task") or message.params.get("text") or "")
    return {
        "messageId": message.message_id,
        "role": "ROLE_USER",
        "parts": [{"text": task_text, "mediaType": "text/plain"}],
        "metadata": {
            "sourceAgentId": message.source_agent_id,
            "targetAgentId": message.target_agent_id,
            "traceId": message.trace_id,
            **message.metadata,
        },
    }


def _official_method(method: str) -> str:
    if method in {"", "execute_task", "message/send", A2A_METHOD_SEND_MESSAGE}:
        return A2A_METHOD_SEND_MESSAGE
    if method in {"tasks/get", A2A_METHOD_GET_TASK}:
        return A2A_METHOD_GET_TASK
    if method in {"tasks/cancel", A2A_METHOD_CANCEL_TASK}:
        return A2A_METHOD_CANCEL_TASK
    return method


def _error_info(reason: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": reason,
        "domain": "a2a-protocol.org",
        "metadata": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **(metadata or {}),
        },
    }


def _build_params(method: str, message: ProtocolMessage) -> dict[str, Any]:
    if method == A2A_METHOD_SEND_MESSAGE:
        return {
            "message": build_a2a_message(message),
            "configuration": {
                "acceptedOutputModes": ["text/plain", "application/json"],
                "returnImmediately": False,
            },
            "metadata": {
                "payload": message.params.get("payload", {}),
                "ioaMethod": message.method,
            },
        }
    if method in {A2A_METHOD_GET_TASK, A2A_METHOD_CANCEL_TASK}:
        task_id = message.params.get("id") or message.params.get("taskId") or message.trace_id
        params = {"id": task_id}
        if message.metadata:
            params["metadata"] = message.metadata
        return params
    return message.params


def _decode_send_message(data: dict[str, Any]) -> ProtocolMessage:
    params = data.get("params", {}) or {}
    message = params.get("message", {}) if isinstance(params, dict) else {}
    metadata = message.get("metadata", {}) if isinstance(message, dict) else {}
    request_metadata = params.get("metadata", {}) if isinstance(params, dict) else {}
    return ProtocolMessage(
        message_id=str(message.get("messageId") or data.get("id", "")),
        source_protocol=ProtocolType.A2A,
        target_protocol=ProtocolType.A2A,
        source_agent_id=metadata.get("sourceAgentId", ""),
        target_agent_id=metadata.get("targetAgentId", ""),
        trace_id=metadata.get("traceId", ""),
        method="execute_task",
        params={
            "task": _extract_parts_text(message.get("parts", [])),
            "payload": request_metadata.get("payload", {}),
        },
        metadata={
            **metadata,
            "a2a_method": A2A_METHOD_SEND_MESSAGE,
            "contextId": message.get("contextId", ""),
            "taskId": message.get("taskId", ""),
        },
    )


def _extract_artifact_text(task: dict[str, Any]) -> str:
    artifacts = task.get("artifacts", [])
    artifact = _first(artifacts, {})
    if not isinstance(artifact, dict):
        return ""
    return _extract_parts_text(artifact.get("parts", []))


def _extract_parts_text(parts: Any) -> str:
    if not isinstance(parts, list):
        return ""
    texts: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if "text" in part:
            texts.append(str(part["text"]))
        elif part.get("kind") == "text" and "text" in part:
            texts.append(str(part["text"]))
        elif "data" in part:
            texts.append(str(part["data"]))
    return "\n".join(texts)


def _first(items: Any, default: Any) -> Any:
    if isinstance(items, list) and items:
        return items[0]
    return default
