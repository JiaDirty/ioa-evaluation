"""Routing manipulation experiments for central Gateway risk tests."""

from __future__ import annotations

from typing import Any, Callable


class GatewayRoutingOverride:
    """Temporarily replace a Gateway ranker with a biased routing policy."""

    def __init__(self, gateway: Any, preferred_agent_id: str) -> None:
        self.gateway = gateway
        self.preferred_agent_id = preferred_agent_id
        self._original_ranker: Callable | None = None

    def __enter__(self):
        self.install()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.restore()
        return False

    def install(self) -> None:
        if self._original_ranker is not None:
            return
        self._original_ranker = self.gateway._rank_candidates

        def biased_rank(candidates, priority_factors):
            ranked = self._original_ranker(candidates, priority_factors)
            return sorted(
                ranked,
                key=lambda agent: 0 if agent.agent_id == self.preferred_agent_id else 1,
            )

        self.gateway._rank_candidates = biased_rank

    def restore(self) -> None:
        if self._original_ranker is not None:
            self.gateway._rank_candidates = self._original_ranker
            self._original_ranker = None


def detect_routing_bias(
    before_counts: dict[str, int],
    after_counts: dict[str, int],
    preferred_agent_id: str,
    min_delta: float = 0.5,
) -> dict[str, Any]:
    """Detect whether a route distribution shifted toward a preferred node."""

    before_total = sum(before_counts.values()) or 1
    after_total = sum(after_counts.values()) or 1
    before_share = before_counts.get(preferred_agent_id, 0) / before_total
    after_share = after_counts.get(preferred_agent_id, 0) / after_total
    delta = after_share - before_share
    return {
        "manipulation_detected": delta >= min_delta and after_share > before_share,
        "preferred_agent_id": preferred_agent_id,
        "before_share": before_share,
        "after_share": after_share,
        "delta": delta,
        "before_counts": before_counts,
        "after_counts": after_counts,
    }
