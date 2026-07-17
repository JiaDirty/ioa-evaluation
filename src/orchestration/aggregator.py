"""Artifact aggregation helpers."""

from __future__ import annotations

from typing import Any

from ..core.data_models import Artifact
from .models import OrchestrationPlan


class ArtifactAggregator:
    def aggregate(
        self,
        *,
        task_id: str,
        trace_id: str,
        gateway_id: str,
        artifacts: list[Artifact],
        plan: OrchestrationPlan,
    ) -> Artifact:
        contributions: list[dict[str, Any]] = []
        text_parts: list[str] = []
        for artifact in artifacts:
            text = artifact.content.get("text") if isinstance(artifact.content, dict) else str(artifact.content)
            text_parts.append(f"[{artifact.producer_agent_id}] {text}")
            contributions.append({
                "agent_id": artifact.producer_agent_id,
                "role": "selected_agent",
                "artifact_id": artifact.artifact_id,
                "summary": text[:160],
            })
        return Artifact(
            task_id=task_id,
            producer_agent_id=gateway_id,
            protocol="orchestration",
            artifact_type="structured_report",
            content={
                "text": "\n\n".join(text_parts),
                "contributions": contributions,
            },
            content_type="application/json",
            source_agent_id=gateway_id,
            source_task_id=task_id,
            safe=all(artifact.safe for artifact in artifacts),
            agent_contributions=contributions,
            metadata={
                "trace_id": trace_id,
                "plan": plan.model_dump(mode="json"),
                "source_artifact_ids": [artifact.artifact_id for artifact in artifacts],
            },
        )
