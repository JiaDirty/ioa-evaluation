"""Rumor tracker for rumor spread evaluation.

Tracks each rumor_id through the propagation chain, recording
text changes, confidence changes, verification status, and
forwarding behavior at each hop.
"""

from __future__ import annotations

from typing import Any


class RumorTracker:
    """Track rumor propagation across agent hops."""

    def __init__(self, rumor_id: str):
        self.rumor_id = rumor_id
        self.hops: list[dict[str, Any]] = []

    def record_hop(
        self,
        role_id: str,
        *,
        seen: bool | None = None,
        disposition: str = "not_assessed",
        current_text: str,
        source_type: str = "",
        verification_status: str = "unverified",
        confidence: float = 0.0,
        forwarded: bool | None = None,
        used_for_action: bool | None = None,
        business_action_changed: bool | None = None,
        parent_artifact_id: str = "",
        current_artifact_id: str = "",
    ) -> dict[str, Any]:
        hop = {
            "rumor_id": self.rumor_id,
            "hop_index": len(self.hops),
            "role_id": role_id,
            "seen": seen,
            "disposition": disposition,
            "current_text": current_text,
            "source_type": source_type,
            "verification_status": verification_status,
            "confidence": confidence,
            "forwarded": forwarded,
            "used_for_action": used_for_action,
            "business_action_changed": business_action_changed,
            "parent_artifact_id": parent_artifact_id,
            "current_artifact_id": current_artifact_id,
        }
        self.hops.append(hop)
        return hop

    def text_evolution(self) -> list[str]:
        return [h["current_text"][:100] for h in self.hops]

    def confidence_trend(self) -> list[float]:
        return [h["confidence"] for h in self.hops]

    def was_forwarded_by(self, role_id: str) -> bool:
        for h in self.hops:
            if h["role_id"] == role_id:
                return bool(h.get("forwarded"))
        return False

    def verification_count(self) -> int:
        return sum(1 for h in self.hops if h.get("verification_status") == "verified")

    def unverified_forward_count(self) -> int:
        return sum(
            1 for h in self.hops
            if h.get("verification_status") == "unverified" and h.get("forwarded")
        )
