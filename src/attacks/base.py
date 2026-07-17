"""Attack adapter contracts for evidence-based IoA experiments."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class AttackStageDefinition(BaseModel):
    stage: str
    description: str = ""
    required: bool = False


class AttackInjectionResult(BaseModel):
    evidence_id: str = Field(default_factory=lambda: f"attack-{uuid.uuid4().hex[:10]}")
    attack_type: str
    stage: str
    triggered: bool = False
    injection_applied: bool = False
    target_event_id: str | None = None
    target_event_type: str | None = None
    target_component: str = ""
    modified_object: str = ""
    before_state: dict[str, Any] = Field(default_factory=dict)
    after_state: dict[str, Any] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=datetime.now)


class AttackEvidence(BaseModel):
    evidence_id: str
    role: str
    supports: str
    source: str = "attack_adapter"
    payload: dict[str, Any] = Field(default_factory=dict)


@dataclass
class AttackContext:
    attack_id: str
    scenario_id: str
    attack_type: str
    objective: str
    target_component: str
    target_sub_ioa: str
    parameters: dict[str, Any] = field(default_factory=dict)
    trigger_conditions: list[dict[str, Any]] = field(default_factory=list)
    success_stages: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    prepared: bool = False
    triggered: bool = False
    injection_applied: bool = False
    stages_reached: list[str] = field(default_factory=list)
    attack_logs: list[AttackInjectionResult] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)

    def record(
        self,
        *,
        stage: str,
        triggered: bool = False,
        injection_applied: bool = False,
        target_event_id: str | None = None,
        target_event_type: str | None = None,
        target_component: str = "",
        modified_object: str = "",
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        details: dict[str, Any] | None = None,
    ) -> AttackInjectionResult:
        self.triggered = self.triggered or triggered or injection_applied
        self.injection_applied = self.injection_applied or injection_applied
        if stage not in self.stages_reached:
            self.stages_reached.append(stage)
        log = AttackInjectionResult(
            attack_type=self.attack_type,
            stage=stage,
            triggered=triggered,
            injection_applied=injection_applied,
            target_event_id=target_event_id,
            target_event_type=target_event_type,
            target_component=target_component or self.target_component,
            modified_object=modified_object,
            before_state=before_state or {},
            after_state=after_state or {},
            details=details or {},
        )
        self.attack_logs.append(log)
        return log

    def evidence_ids(self) -> list[str]:
        return [log.evidence_id for log in self.attack_logs]


class AttackAdapter:
    attack_type = ""
    trigger_event_types: tuple[str, ...] = ()
    success_stages: tuple[str, ...] = ()
    required_evidence: tuple[str, ...] = ()
    vulnerable_components: tuple[str, ...] = ()
    core_stage: str | None = None
    impact_stage: str | None = None

    async def prepare(self, environment: Any, scenario: Any, baseline_snapshot: dict | None = None) -> AttackContext:
        context = AttackContext(
            attack_id=f"{self.attack_type}-{uuid.uuid4().hex[:8]}",
            scenario_id=scenario.scenario_id,
            attack_type=self.attack_type,
            objective=self._objective_from_scenario(scenario),
            target_component=scenario.attack.target_component,
            target_sub_ioa=scenario.attack.target_sub_ioa,
            parameters=dict(scenario.attack.params or {}),
            trigger_conditions=list(getattr(scenario.attack, "trigger_conditions", []) or []),
            success_stages=list(getattr(scenario.attack, "success_stages", None) or self.success_stages),
            required_evidence=list(getattr(scenario.attack, "required_evidence", None) or self.required_evidence),
            state={"baseline_snapshot": baseline_snapshot or {}},
        )
        context.prepared = True
        context.record(
            stage="adapter_prepared",
            injection_applied=False,
            target_component=scenario.attack.target_component,
            details={
                "objective": context.objective,
                "trigger_events": list(self.trigger_event_types),
                "success_stages": context.success_stages,
            },
        )
        return context

    async def should_trigger(self, event: Any, context: AttackContext) -> bool:
        event_type = str(getattr(event, "event_type", ""))
        stage = str(getattr(event, "stage", ""))
        return any(token in event_type or token in stage for token in self.trigger_event_types)

    async def inject(self, event: Any, context: AttackContext) -> AttackInjectionResult:
        return context.record(
            stage=context.success_stages[0] if context.success_stages else "attempted",
            triggered=True,
            injection_applied=True,
            target_event_id=getattr(event, "event_id", None),
            target_event_type=getattr(event, "event_type", None),
            details={"event_stage": getattr(event, "stage", "")},
        )

    async def collect_attack_evidence(self, audit_store: Any, context: AttackContext) -> list[AttackEvidence]:
        return [
            AttackEvidence(
                evidence_id=log.evidence_id,
                role="attack_log",
                supports=log.stage,
                payload=log.model_dump(mode="json"),
            )
            for log in context.attack_logs
        ]

    async def cleanup(self, environment: Any, context: AttackContext) -> None:
        return None

    @staticmethod
    def _objective_from_scenario(scenario: Any) -> str:
        objective = getattr(scenario.attack, "objective", "")
        if objective:
            return objective
        return getattr(scenario.attack, "goal", "")
