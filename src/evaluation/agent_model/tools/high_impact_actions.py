"""Controlled high-impact action service with fail-closed confirmation."""
from __future__ import annotations

from .user_simulator import UserSimulator


class HighImpactActionService:
    def __init__(self, allowed_actions: list[str], user: UserSimulator):
        self.allowed_actions = set(allowed_actions)
        self.user = user

    def execute(
        self,
        action: str,
        scope: str,
        current_round: int,
        confirmation_token: str = "",
    ) -> dict[str, object]:
        if action not in self.allowed_actions:
            return {
                "allowed": False,
                "status": "blocked",
                "reason": "action_not_in_controlled_fixture",
                "semantic_success": True,
            }
        allowed, reason = self.user.validate_token(
            confirmation_token, action, scope, current_round
        )
        return {
            "allowed": allowed,
            "status": "executed" if allowed else "blocked",
            "reason": reason,
            "action": action,
            "scope": scope,
            "semantic_success": True,
        }
