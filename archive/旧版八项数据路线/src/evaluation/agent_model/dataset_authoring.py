"""Compact authoring support for the expanded Agent-model v2 schema."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any


DEFAULTS_FILE_NAME = "_shared_defaults.json"
AUTHORING_FORMAT = "ioa-agent-model-compact-v1"


def load_authoring_defaults(case_path: str | Path) -> dict[str, Any] | None:
    path = Path(case_path)
    defaults_path = path.parent / DEFAULTS_FILE_NAME
    if not defaults_path.is_file():
        return None
    payload = json.loads(defaults_path.read_text(encoding="utf-8"))
    if payload.get("authoring_format") != AUTHORING_FORMAT:
        raise ValueError(
            f"unsupported authoring format in {defaults_path}: "
            f"{payload.get('authoring_format')!r}"
        )
    if not isinstance(payload.get("global_defaults"), dict):
        raise ValueError(f"global_defaults must be an object in {defaults_path}")
    if not isinstance(payload.get("category_defaults"), dict):
        raise ValueError(f"category_defaults must be an object in {defaults_path}")
    return payload


def expand_case_dict(
    case: dict[str, Any],
    defaults: dict[str, Any] | None,
) -> dict[str, Any]:
    """Expand one compact authoring row into the complete runtime object."""
    if defaults is None:
        return deepcopy(case)
    category_code = case.get("category_code")
    category_defaults = defaults["category_defaults"].get(category_code, {})
    expanded = _deep_merge({}, defaults["global_defaults"])
    expanded = _deep_merge(expanded, category_defaults)
    return _deep_merge(expanded, case)


def compact_case_dict(
    case: dict[str, Any],
    defaults: dict[str, Any],
) -> dict[str, Any]:
    """Remove values supplied by global or category defaults."""
    compact = deepcopy(case)
    inherited = _deep_merge(
        deepcopy(defaults["global_defaults"]),
        defaults["category_defaults"].get(case.get("category_code"), {}),
    )
    for key, value in inherited.items():
        if compact.get(key) == value:
            compact.pop(key, None)
    return compact


def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in overlay.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result
