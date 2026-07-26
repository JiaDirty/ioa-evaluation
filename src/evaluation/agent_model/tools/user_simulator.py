"""Deterministic user-confirmation fixture for high-impact actions."""
from __future__ import annotations

import hashlib
from typing import Any


class UserSimulator:
    def __init__(self, approvals: list[dict[str, Any]] | None = None):
        self._approvals = list(approvals or [])
        self._tokens: dict[str, dict[str, Any]] = {}

    def request_confirmation(
        self, action: str, scope: str, current_round: int
    ) -> dict[str, Any]:
        approved = next((
            item for item in self._approvals
            if item.get("action") == action and item.get("scope") == scope
        ), None)
        if approved is None:
            return {
                "actor_type": "user_simulator",
                "status": "confirmation_required",
                "confirmed": False,
                "action": action,
                "scope": scope,
                "semantic_success": True,
            }
        expiry_round = int(approved.get("expiry_round", current_round))
        raw = f"{action}|{scope}|{current_round}|{expiry_round}"
        token = "confirm-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        self._tokens[token] = {
            "action": action,
            "scope": scope,
            "expiry_round": expiry_round,
        }
        return {
            "actor_type": "user_simulator",
            "status": "confirmed",
            "confirmed": True,
            "confirmation_token": token,
            "action": action,
            "scope": scope,
            "expiry_round": expiry_round,
            "semantic_success": True,
        }

    def validate_token(
        self, token: str, action: str, scope: str, current_round: int
    ) -> tuple[bool, str]:
        record = self._tokens.get(token)
        if record is None:
            return False, "missing_or_unknown_confirmation_token"
        if record["action"] != action or record["scope"] != scope:
            return False, "confirmation_scope_mismatch"
        if current_round > record["expiry_round"]:
            return False, "confirmation_token_expired"
        return True, "confirmed"
