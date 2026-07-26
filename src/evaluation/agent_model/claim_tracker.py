"""Claim tracker for cascade propagation evaluation.

Tracks each claim_id through the agent chain, recording
whether each agent read, accepted, rejected, verified, forwarded,
or acted on the claim.
"""

from __future__ import annotations

from typing import Any


class ClaimTracker:
    """Track claim propagation across agent hops."""

    def __init__(self, claim_id: str):
        self.claim_id = claim_id
        self.hops: list[dict[str, Any]] = []

    def record_hop(
        self,
        role_id: str,
        source_artifact_id: str,
        current_artifact_id: str,
        *,
        seen: bool | None = None,
        accepted: bool | None = None,
        rejected: bool = False,
        uncertain: bool = False,
        verification_requested: bool = False,
        confidence: float = 0.0,
        forwarded: bool | None = None,
        business_action_changed: bool | None = None,
    ) -> dict[str, Any]:
        hop = {
            "claim_id": self.claim_id,
            "hop_index": len(self.hops),
            "role_id": role_id,
            "source_artifact_id": source_artifact_id,
            "current_artifact_id": current_artifact_id,
            "seen": seen,
            "accepted": accepted,
            "rejected": rejected,
            "uncertain": uncertain,
            "verification_requested": verification_requested,
            "confidence": confidence,
            "forwarded": forwarded,
            "business_action_changed": business_action_changed,
        }
        self.hops.append(hop)
        return hop

    def get_chain(self) -> list[dict[str, Any]]:
        return list(self.hops)

    def was_accepted_by(self, role_id: str) -> bool:
        for h in self.hops:
            if h["role_id"] == role_id:
                return bool(h.get("accepted"))
        return False

    def propagation_depth(self) -> int:
        return sum(1 for h in self.hops if h.get("forwarded"))
