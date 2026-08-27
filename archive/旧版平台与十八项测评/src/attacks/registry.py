"""Attack adapter registry."""

from __future__ import annotations

from typing import Type

from .adapters import (
    AccountabilityBreakAdapter,
    AgencyErosionAdapter,
    BehaviorInferenceAdapter,
    CascadePropagationAdapter,
    DelegationDriftAdapter,
    DiscussionDistortionAdapter,
    EcosystemConsensusAdapter,
    IdentitySpoofingAdapter,
    IncentiveMismatchAdapter,
    InteropMismatchAdapter,
    JudgmentSurrenderAdapter,
    NegotiationPollutionAdapter,
    NodeManipulationAdapter,
    NormDriftAdapter,
    RegistryDistortionAdapter,
    ReputationMonopolyAdapter,
    RumorSpreadAdapter,
    StructureExposureAdapter,
)
from .base import AttackAdapter


ADAPTER_CLASSES: tuple[type[AttackAdapter], ...] = (
    IdentitySpoofingAdapter,
    RegistryDistortionAdapter,
    DelegationDriftAdapter,
    NegotiationPollutionAdapter,
    InteropMismatchAdapter,
    AccountabilityBreakAdapter,
    CascadePropagationAdapter,
    StructureExposureAdapter,
    BehaviorInferenceAdapter,
    EcosystemConsensusAdapter,
    RumorSpreadAdapter,
    NormDriftAdapter,
    IncentiveMismatchAdapter,
    ReputationMonopolyAdapter,
    NodeManipulationAdapter,
    JudgmentSurrenderAdapter,
    DiscussionDistortionAdapter,
    AgencyErosionAdapter,
)

ATTACK_TYPE_ALIASES = {
    "sybil_social_engineering": "identity_spoofing",
    "knowledge_injection": "ecosystem_consensus",
    "protocol_downgrade": "negotiation_pollution",
    "reputation_manipulation": "reputation_monopoly",
    "semantic_mismatch": "interop_mismatch",
    "audit_gap_injection": "accountability_break",
    "malicious_artifact_propagation": "cascade_propagation",
    "metadata_observation": "structure_exposure",
    "rumor_injection": "rumor_spread",
}


class AttackAdapterRegistry:
    def __init__(self) -> None:
        self._classes: dict[str, Type[AttackAdapter]] = {}
        for cls in ADAPTER_CLASSES:
            self.register(cls)

    def register(self, adapter_cls: Type[AttackAdapter]) -> None:
        self._classes[adapter_cls.attack_type] = adapter_cls

    def canonicalize(self, attack_type: str) -> str:
        return ATTACK_TYPE_ALIASES.get(attack_type, attack_type)

    def create(self, attack_type: str) -> AttackAdapter:
        canonical = self.canonicalize(attack_type)
        if canonical not in self._classes:
            raise KeyError(f"No AttackAdapter registered for attack type: {attack_type}")
        return self._classes[canonical]()

    def list_attack_types(self) -> list[str]:
        return sorted(self._classes)


DEFAULT_ATTACK_ADAPTER_REGISTRY = AttackAdapterRegistry()
