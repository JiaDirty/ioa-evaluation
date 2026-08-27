"""Build AttackEvaluationBundle objects from runner state."""

from __future__ import annotations

from typing import Any

from src.evaluation.attack_evaluation_bundle import AttackEvaluationBundle


EVENT_GROUPS = {
    "registry": ("registry", "discovery", "candidate", "registration"),
    "gateway": ("gateway",),
    "protocol": ("protocol", "negotiation", "translation"),
    "authorization": ("authorization", "policy"),
    "delegation": ("delegation",),
    "agent": ("agent", "runtime"),
    "tool": ("tool",),
    "knowledge": ("knowledge",),
    "reputation": ("reputation",),
    "reward": ("reward",),
    "routing": ("routing", "candidate"),
    "human": ("human", "checkpoint"),
    "synthesis": ("synthesis",),
}


def build_attack_evaluation_bundle(
    *,
    scenario: Any,
    task_id: str,
    execution_mode: str,
    model_metadata: dict[str, Any],
    attack_context: Any,
    task_prompt: str,
    task_spec: dict[str, Any],
    initial_graph: dict[str, Any],
    graph_revisions: list[dict[str, Any]],
    final_state: dict[str, Any],
    events: list[dict[str, Any]],
    baseline_snapshot: dict[str, Any],
    before_attack_snapshot: dict[str, Any],
    after_attack_snapshot: dict[str, Any],
    final_snapshot: dict[str, Any],
) -> AttackEvaluationBundle:
    grouped: dict[str, list[dict[str, Any]]] = {key: [] for key in EVENT_GROUPS}
    for event in events:
        haystack = " ".join(
            str(event.get(key, "")).lower()
            for key in ["event_type", "stage", "actor_type", "component"]
        )
        for group, tokens in EVENT_GROUPS.items():
            if any(token in haystack for token in tokens):
                grouped[group].append(event)

    evidence_index: dict[str, dict[str, Any]] = {}
    for event in events:
        event_id = event.get("event_id")
        if event_id:
            evidence_index[str(event_id)] = {"source": "event", "event": event}
    for log in attack_context.attack_logs:
        evidence_index[log.evidence_id] = {
            "source": "attack_log",
            "attack_log": log.model_dump(mode="json"),
        }

    return AttackEvaluationBundle(
        experiment={
            "scenario_id": scenario.scenario_id,
            "task_id": task_id,
            "execution_mode": execution_mode,
            "model_metadata": model_metadata,
        },
        attack_specification={
            "attack_type": attack_context.attack_type,
            "objective": attack_context.objective,
            "trigger_conditions": attack_context.trigger_conditions,
            "success_stages": attack_context.success_stages,
            "required_evidence": attack_context.required_evidence,
        },
        attack_injection={
            "prepared": attack_context.prepared,
            "triggered": attack_context.triggered,
            "injection_applied": attack_context.injection_applied,
            "logs": [log.model_dump(mode="json") for log in attack_context.attack_logs],
        },
        task_execution={
            "prompt": task_prompt,
            "task_spec": task_spec,
            "initial_graph": initial_graph,
            "graph_revisions": graph_revisions,
            "final_state": final_state,
        },
        events=grouped,
        state_snapshots={
            "baseline": baseline_snapshot,
            "before_attack": before_attack_snapshot,
            "after_attack": after_attack_snapshot,
            "final": final_snapshot,
        },
        evidence_index=evidence_index,
    )
