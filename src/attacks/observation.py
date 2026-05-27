"""External attacker observation model for IoA inference tests.

The attacker must not receive full internal audit entries. This module derives
limited observations that approximate externally visible metadata: timing,
source/target domains, action class, success/failure hints, and coarse protocol.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from ..core.data_models import AuditEntry


@dataclass(frozen=True)
class ExternalObservation:
    timestamp: str
    trace_id: str
    source_domain: str
    target_domain_hint: str
    action: str
    protocol: str | None
    status_hint: str


@dataclass(frozen=True)
class NetworkObservationEvent:
    """Externally visible network metadata captured outside internal audit."""

    timestamp: datetime | str
    trace_id: str
    source_domain: str
    target_domain_hint: str = "unknown"
    protocol: str | None = None
    status_hint: str = "observed"
    action: str = "network_post"


class ExternalObservationModel:
    """Convert internal audit entries into attacker-visible observations."""

    def __init__(self, expose_agent_ids: bool = False) -> None:
        self.expose_agent_ids = expose_agent_ids

    def from_audit_entries(self, entries: list[AuditEntry]) -> list[ExternalObservation]:
        observations: list[ExternalObservation] = []
        for entry in entries:
            target_hint = self._target_hint(entry)
            observations.append(ExternalObservation(
                timestamp=entry.timestamp.isoformat(),
                trace_id=entry.trace_id,
                source_domain=entry.sub_ioa_id,
                target_domain_hint=target_hint,
                action=entry.action.value,
                protocol=entry.protocol_type.value if entry.protocol_type else None,
                status_hint=self._status_hint(entry.details),
            ))
        return observations

    def from_network_events(self, events: list[NetworkObservationEvent]) -> list[ExternalObservation]:
        """Convert externally captured network events into observations.

        This is the preferred input for structure-exposure and behavior-inference
        experiments. `from_audit_entries` remains available only as a testbed
        bridge when packet capture or proxy logs are not available.
        """
        observations: list[ExternalObservation] = []
        for event in events:
            timestamp = (
                event.timestamp.isoformat()
                if isinstance(event.timestamp, datetime)
                else str(event.timestamp)
            )
            observations.append(ExternalObservation(
                timestamp=timestamp,
                trace_id=event.trace_id,
                source_domain=event.source_domain,
                target_domain_hint=event.target_domain_hint,
                action=event.action,
                protocol=event.protocol,
                status_hint=event.status_hint,
            ))
        return observations

    def infer_gateway_exposure(self, observations: list[ExternalObservation]) -> dict[str, Any]:
        domain_counts: dict[str, int] = {}
        relay_count = 0
        for obs in observations:
            domain_counts[obs.source_domain] = domain_counts.get(obs.source_domain, 0) + 1
            if obs.action == "relay":
                relay_count += 1
        if not domain_counts:
            return {"exposed": False, "confidence": 0.0, "reason": "no observations"}
        top_domain, top_count = max(domain_counts.items(), key=lambda item: item[1])
        total = sum(domain_counts.values())
        confidence = top_count / total
        return {
            "exposed": confidence > 0.6 and relay_count > 0,
            "confidence": confidence,
            "top_domain": top_domain,
            "relay_count": relay_count,
        }

    def infer_behavior_pattern(self, observations: list[ExternalObservation]) -> dict[str, Any]:
        domain_pairs: dict[str, int] = {}
        for obs in observations:
            if obs.target_domain_hint and obs.target_domain_hint != "unknown":
                pair = f"{obs.source_domain}->{obs.target_domain_hint}"
                domain_pairs[pair] = domain_pairs.get(pair, 0) + 1
        if not domain_pairs:
            return {"inferable": False, "confidence": 0.0, "reason": "no domain pairs"}
        top_pair, top_count = max(domain_pairs.items(), key=lambda item: item[1])
        total = sum(domain_pairs.values())
        confidence = top_count / total
        return {
            "inferable": confidence > 0.5,
            "confidence": confidence,
            "top_pair": top_pair,
            "domain_pairs": domain_pairs,
        }

    def _target_hint(self, entry: AuditEntry) -> str:
        if "target_sub_ioa" in entry.details:
            return str(entry.details["target_sub_ioa"])
        if self.expose_agent_ids and entry.target_agent_id:
            return entry.target_agent_id
        return "unknown"

    @staticmethod
    def _status_hint(details: dict[str, Any]) -> str:
        if details.get("safe") is False:
            return "unsafe"
        if details.get("authorized") is False:
            return "denied"
        return "observed"
