"""Fail-closed, deterministic projection of state into agent-visible context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


class ContextOverflowError(ValueError):
    code = "INVALID_CONTEXT_OVERFLOW"


class TaintedContextError(ValueError):
    code = "INVALID_TAINTED_CONTEXT"


@dataclass(frozen=True)
class ContextProjectionPolicy:
    version: str = "context-projection-v1"
    max_tokens: int = 6000
    required_context_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextProjectionResult:
    value: Any
    estimated_input_tokens: int
    projected_tokens: int
    removed_paths: tuple[str, ...] = ()
    required_complete: bool = True
    policy_version: str = "context-projection-v1"


def _tokens(value: Any) -> int:
    return max(1, (len(json.dumps(value, ensure_ascii=False, default=str)) + 3) // 4)


def _scan_taint(value: Any, path: str = "") -> str | None:
    if isinstance(value, dict):
        if value.get("__tainted__") is True or value.get("taint") == "evaluation_only":
            return path or "$"
        for key, child in value.items():
            found = _scan_taint(child, f"{path}.{key}" if path else str(key))
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            found = _scan_taint(child, f"{path}[{index}]")
            if found:
                return found
    return None


def project_context(
    value: Any,
    policy: ContextProjectionPolicy,
    *,
    required_paths: tuple[str, ...] | None = None,
) -> ContextProjectionResult:
    """Validate and serialize visible state without silent truncation.

    Projection currently preserves the complete allowlisted value.  A future
    artifact-backed summarizer may reduce optional fields, but it must return
    explicit removed paths; required fields are never character-truncated.
    """
    taint_path = _scan_taint(value)
    if taint_path:
        raise TaintedContextError(f"evaluation-only value at {taint_path}")
    estimated = _tokens(value)
    required = required_paths or policy.required_context_paths
    missing = [path for path in required if not _has_path(value, path)]
    if missing:
        raise ContextOverflowError(f"required context missing: {missing}")
    if estimated > policy.max_tokens:
        raise ContextOverflowError(
            f"context requires {estimated} tokens, limit is {policy.max_tokens}"
        )
    return ContextProjectionResult(
        value=value,
        estimated_input_tokens=estimated,
        projected_tokens=estimated,
        required_complete=True,
        policy_version=policy.version,
    )


def _has_path(value: Any, path: str) -> bool:
    current = value
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        else:
            return False
    return current is not None
