"""Synthesis decision component for traceable final answers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from ..core.data_models import Artifact, TaskSpec


class SynthesisDecision(BaseModel):
    answer: Any
    evidence_map: dict[str, list[str]] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    needs_more_work: bool = False
    suggested_followup_capabilities: list[str] = Field(default_factory=list)


class SynthesisAgent:
    name = "SynthesisAgent"

    def __init__(self, model_client: Any | None = None) -> None:
        self.model_client = model_client

    def synthesize(
        self,
        *,
        task_spec: TaskSpec,
        artifacts: list[Artifact],
        completion_criteria: list[str] | None = None,
    ) -> SynthesisDecision:
        evidence_map: dict[str, list[str]] = {}
        answer_sections: list[str] = []
        conflicts: list[str] = []
        seen_texts: dict[str, str] = {}

        for artifact in artifacts:
            text = artifact.content.get("text") if isinstance(artifact.content, dict) else str(artifact.content)
            key = artifact.producer_agent_id or artifact.source_agent_id or artifact.artifact_id
            evidence_map.setdefault(key, []).append(artifact.artifact_id)
            answer_sections.append(f"[{artifact.artifact_id}] {text}")
            normalized = text.strip().lower()
            if normalized and normalized in seen_texts and seen_texts[normalized] != key:
                conflicts.append(f"Repeated identical claim from {seen_texts[normalized]} and {key}")
            elif normalized:
                seen_texts[normalized] = key

        required_count = len([req for req in task_spec.capability_requirements if req.required])
        enough_artifacts = len(artifacts) >= max(1, min(required_count, len(task_spec.capability_requirements)))
        limitations = [] if enough_artifacts else ["Some required capability nodes did not produce artifacts."]
        if task_spec.human_checkpoints:
            limitations.append("Side-effect actions require explicit human checkpoint approval before execution.")

        return SynthesisDecision(
            answer={
                "text": "\n\n".join(answer_sections),
                "goal": task_spec.normalized_goal,
                "criteria": completion_criteria or task_spec.completion_criteria,
            },
            evidence_map=evidence_map,
            conflicts=conflicts,
            unresolved_questions=[] if enough_artifacts else ["Missing required capability evidence."],
            limitations=limitations,
            confidence=0.72 if enough_artifacts else 0.45,
            needs_more_work=not enough_artifacts,
            suggested_followup_capabilities=[] if enough_artifacts else [
                req.capability for req in task_spec.capability_requirements
            ],
        )
