"""Deterministic tool authorization helpers."""

from __future__ import annotations

from .models import ToolCall, ToolDescriptor


class ToolPolicyEngine:
    def authorize(self, descriptor: ToolDescriptor, call: ToolCall) -> tuple[bool, str]:
        granted = set(call.granted_scopes)
        missing = [scope for scope in descriptor.required_scopes if scope not in granted and "*" not in granted]
        if missing:
            return False, f"missing tool scopes: {', '.join(missing)}"
        if descriptor.risk_level == "critical" and "critical_tool" not in granted and "*" not in granted:
            return False, "critical tool requires critical_tool scope"
        if descriptor.risk_level in {"high", "critical"} and "high_risk_tool" not in granted and "*" not in granted:
            return False, "high risk tool requires high_risk_tool scope"
        return True, "allowed"
