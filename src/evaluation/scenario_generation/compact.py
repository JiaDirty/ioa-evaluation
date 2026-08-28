"""Lossless authoring representation for generated business scenarios.

The runtime keeps the fully expanded ``BusinessCaseSpec`` contract.  This
module only removes mechanical repetition from authoring/generation payloads;
``expand_compact_case`` restores a normal case before any runtime validation.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ..business_protocol.models import BusinessCaseSpec


_CONDITIONS = ("baseline", "mechanism", "recovery")
_STEP_OPTIONAL_DEFAULTS = {
    "raw_user_message": None,
    "history_fixtures": {},
    "current_times": {},
    "metadata": {},
}


def _stable(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _collapse_conditions(value: dict[str, Any]) -> dict[str, Any]:
    present = [value.get(c) for c in _CONDITIONS]
    if all(item is not None for item in present) and all(
        _stable(item) == _stable(present[0]) for item in present[1:]
    ):
        return {"shared": deepcopy(present[0])}
    return deepcopy(value)


def _expand_conditions(value: dict[str, Any], *, field: str) -> dict[str, Any]:
    unknown = [key for key in value if key != "shared" and key not in _CONDITIONS]
    if unknown:
        raise ValueError(f"compact {field} has unknown condition keys: {unknown}")
    shared = value.get("shared")
    explicit = [condition for condition in _CONDITIONS if condition in value]
    if shared is not None and explicit:
        raise ValueError(
            f"compact {field} must not mix 'shared' with explicit conditions: {explicit}"
        )
    if shared is not None:
        return {condition: deepcopy(shared) for condition in _CONDITIONS}
    missing = [condition for condition in _CONDITIONS if condition not in value]
    if missing:
        raise ValueError(f"compact {field} is missing conditions: {missing}")
    return {condition: deepcopy(value[condition]) for condition in _CONDITIONS}


def compact_case(case: BusinessCaseSpec | dict[str, Any]) -> dict[str, Any]:
    """Convert an expanded case to the compact authoring representation."""
    source = (
        case.model_dump(mode="json", exclude_none=False)
        if isinstance(case, BusinessCaseSpec)
        else deepcopy(case)
    )
    for flow_name in ("steps", "recovery_steps"):
        for step in source.get(flow_name, []):
            for key, default in _STEP_OPTIONAL_DEFAULTS.items():
                if step.get(key) == default:
                    step.pop(key, None)
            step["inputs"] = _collapse_conditions(step["inputs"])
            for tool in step.get("tools", []):
                if not tool.get("state_updates"):
                    tool.pop("state_updates", None)
                if tool.get("available_conditions") == list(_CONDITIONS):
                    tool.pop("available_conditions", None)
                responses = tool.get("responses", {})
                if responses:
                    tool["responses"] = _collapse_conditions(responses)
    return source


def expand_compact_case(compact: dict[str, Any]) -> BusinessCaseSpec:
    """Expand and validate one compact case into the runtime contract."""
    source = deepcopy(compact)
    for flow_name in ("steps", "recovery_steps"):
        for step in source.get(flow_name, []):
            step["inputs"] = _expand_conditions(step["inputs"], field=f"{flow_name}.inputs")
            for key, default in _STEP_OPTIONAL_DEFAULTS.items():
                step.setdefault(key, deepcopy(default))
            for tool in step.get("tools", []):
                tool["responses"] = _expand_conditions(
                    tool.get("responses", {}), field=f"{flow_name}.tools.responses"
                )
                tool.setdefault("state_updates", {})
                tool.setdefault("available_conditions", list(_CONDITIONS))
    return BusinessCaseSpec.model_validate(source)


def compact_envelope(case: BusinessCaseSpec | dict[str, Any]) -> dict[str, Any]:
    return {"schema_version": "business_case_authoring_v1", "case": compact_case(case)}


def expand_envelope(payload: dict[str, Any]) -> BusinessCaseSpec:
    if payload.get("schema_version") != "business_case_authoring_v1":
        raise ValueError("unsupported compact schema_version")
    if not isinstance(payload.get("case"), dict):
        raise ValueError("compact payload must contain an object case")
    return expand_compact_case(payload["case"])

