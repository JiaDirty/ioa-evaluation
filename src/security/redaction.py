"""Sensitive value redaction for persisted runtime evidence."""

from __future__ import annotations

from typing import Any

REDACTED_VALUE = "***REDACTED***"
SENSITIVE_KEYWORDS = {
    "password",
    "token",
    "api_key",
    "apikey",
    "secret",
    "authorization",
    "cookie",
}


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                redacted[key] = REDACTED_VALUE
            else:
                redacted[key] = redact_sensitive(item)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive(item) for item in value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower().replace("-", "_")
    return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)
