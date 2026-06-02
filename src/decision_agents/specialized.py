from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel

from .base import DecisionAgent
from .models import (
    AgencyErosionDecision,
    AuditAttributionDecision,
    DecisionContext,
    DelegationDriftDecision,
    DiscussionIntegrityDecision,
    IncentiveAlignmentDecision,
    InteropSemanticMappingDecision,
    NormDriftDecision,
    ReputationFairnessDecision,
    RoutingManipulationDecision,
    RumorAssessmentDecision,
    SensitivityClassificationDecision,
)

OutputT = TypeVar("OutputT", bound=BaseModel)


class _SpecializedDecisionAgent(DecisionAgent[dict[str, Any], OutputT]):
    prompt_instruction: str = ""

    def build_user_prompt(self, decision_input: dict[str, Any], ctx: DecisionContext) -> str:
        return (
            f"{self.prompt_instruction} "
            "Do not authorize execution; provide structured semantic evidence only.\n"
            f"Context: {ctx.model_dump_json()}\n"
            f"Evidence: {json.dumps(decision_input, ensure_ascii=False, default=str)}"
        )


class SensitivityClassifierAgent(
    _SpecializedDecisionAgent[SensitivityClassificationDecision]
):
    name = "SensitivityClassifierAgent"
    output_schema = SensitivityClassificationDecision
    prompt_instruction = (
        "Classify task or content sensitivity, high-impact status, and human review need."
    )


class DelegationDriftAgent(_SpecializedDecisionAgent[DelegationDriftDecision]):
    name = "DelegationDriftAgent"
    output_schema = DelegationDriftDecision
    prompt_instruction = (
        "Assess whether delegated requested scopes drift beyond parent authority."
    )


class InteropSemanticMapperAgent(
    _SpecializedDecisionAgent[InteropSemanticMappingDecision]
):
    name = "InteropSemanticMapperAgent"
    output_schema = InteropSemanticMappingDecision
    prompt_instruction = (
        "Map semantics across protocols and identify lost controls or compensations."
    )


class RumorAssessmentAgent(_SpecializedDecisionAgent[RumorAssessmentDecision]):
    name = "RumorAssessmentAgent"
    output_schema = RumorAssessmentDecision
    prompt_instruction = (
        "Assess unverified claim, rumor propagation, source support, and review need."
    )


class NormDriftAgent(_SpecializedDecisionAgent[NormDriftDecision]):
    name = "NormDriftAgent"
    output_schema = NormDriftDecision
    prompt_instruction = (
        "Assess whether interaction norms drift toward unsafe or non-compliant defaults."
    )


class ReputationFairnessAgent(_SpecializedDecisionAgent[ReputationFairnessDecision]):
    name = "ReputationFairnessAgent"
    output_schema = ReputationFairnessDecision
    prompt_instruction = (
        "Assess reputation concentration, monopoly effects, and fairness remediation."
    )


class IncentiveAlignmentAgent(_SpecializedDecisionAgent[IncentiveAlignmentDecision]):
    name = "IncentiveAlignmentAgent"
    output_schema = IncentiveAlignmentDecision
    prompt_instruction = (
        "Assess whether rewards, prompts, or optimization targets are misaligned."
    )


class RoutingManipulationAgent(_SpecializedDecisionAgent[RoutingManipulationDecision]):
    name = "RoutingManipulationAgent"
    output_schema = RoutingManipulationDecision
    prompt_instruction = (
        "Assess routing-share changes for manipulation, capture, or rebalancing need."
    )


class DiscussionIntegrityAgent(_SpecializedDecisionAgent[DiscussionIntegrityDecision]):
    name = "DiscussionIntegrityAgent"
    output_schema = DiscussionIntegrityDecision
    prompt_instruction = (
        "Assess discussion quality for coordinated distortion or integrity compromise."
    )


class AuditAttributionAgent(_SpecializedDecisionAgent[AuditAttributionDecision]):
    name = "AuditAttributionAgent"
    output_schema = AuditAttributionDecision
    prompt_instruction = (
        "Assess attribution completeness, missing evidence, and audit follow-up need."
    )


class AgencyErosionAgent(_SpecializedDecisionAgent[AgencyErosionDecision]):
    name = "AgencyErosionAgent"
    output_schema = AgencyErosionDecision
    prompt_instruction = (
        "Assess whether interaction patterns erode independent human agency or approval."
    )
