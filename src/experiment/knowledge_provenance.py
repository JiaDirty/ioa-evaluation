"""Knowledge provenance checks for rumor and consensus experiments."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class KnowledgeProvenanceVerdict:
    verified: bool
    reason: str
    missing: list[str] = field(default_factory=list)
    evidence_count: int = 0


class KnowledgeProvenanceVerifier:
    """Validate whether a shared-knowledge claim has usable external provenance."""

    def assess(
        self,
        content: str,
        source_agent_id: str,
        metadata: dict[str, Any] | None = None,
    ) -> KnowledgeProvenanceVerdict:
        metadata = metadata or {}
        source_evidence = metadata.get("source_evidence", [])
        missing = []
        if not source_agent_id:
            missing.append("source_agent_id")
        if not source_evidence:
            missing.append("source_evidence")
        verified_evidence = [
            item for item in source_evidence
            if item.get("verified") is True and (item.get("url") or item.get("document_id"))
        ]
        if source_evidence and not verified_evidence:
            missing.append("verified_source_evidence")
        if missing:
            return KnowledgeProvenanceVerdict(
                verified=False,
                reason="knowledge claim lacks independently verified provenance",
                missing=missing,
                evidence_count=len(source_evidence),
            )
        return KnowledgeProvenanceVerdict(
            verified=True,
            reason="knowledge claim has independently verified provenance",
            evidence_count=len(verified_evidence),
        )
