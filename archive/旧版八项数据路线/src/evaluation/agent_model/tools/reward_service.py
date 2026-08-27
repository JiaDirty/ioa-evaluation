"""Reward service for incentive mismatch evaluation.

Rewards are computed from real agent behavior logs — never self-reported.
Each round's reward is persisted and can be queried by the tested agent.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any


class RewardService:
    """Local deterministic reward computation and query service."""

    def __init__(self):
        self._reward_log: list[dict[str, Any]] = []

    def compute_and_log_reward(
        self,
        run_id: str,
        role_id: str,
        round_index: int,
        policy_version: str,
        behavior_features: dict[str, Any],
        weights: dict[str, float],
    ) -> dict[str, Any]:
        """Compute reward from behavior features and weights, then log it."""
        breakdown = {}
        total = 0.0
        missing_features: list[str] = []
        for feature, weight in weights.items():
            raw_value = behavior_features.get(feature)
            if not isinstance(raw_value, (int, float)):
                missing_features.append(feature)
                breakdown[feature] = {
                    "value": None,
                    "weight": weight,
                    "contribution": None,
                    "reason": "not objectively observable in this run",
                }
                continue
            val = float(raw_value)
            contrib = val * weight
            breakdown[feature] = {"value": val, "weight": weight, "contribution": contrib}
            total += contrib

        entry = {
            "run_id": run_id,
            "role_id": role_id,
            "round_index": round_index,
            "reward_policy_version": policy_version,
            "reward_breakdown": breakdown,
            "behavior_features": behavior_features,
            "total_reward": round(total, 4),
            "reward_complete": not missing_features,
            "missing_weighted_features": missing_features,
            "logged_at": datetime.now().isoformat(),
        }
        self._reward_log.append(entry)
        return entry

    def get_reward_history(
        self,
        role_id: str | None = None,
        run_id: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Return recent reward entries, optionally filtered."""
        results = self._reward_log
        if role_id:
            results = [e for e in results if e.get("role_id") == role_id]
        if run_id:
            results = [e for e in results if e.get("run_id") == run_id]
        return results[-limit:]

    def get_total_reward(
        self, role_id: str | None = None, run_id: str | None = None
    ) -> float:
        history = self.get_reward_history(role_id=role_id, run_id=run_id, limit=9999)
        return sum(e["total_reward"] for e in history)
