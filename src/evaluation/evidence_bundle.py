"""Evidence bundle consumed by deterministic metrics and LLM judges."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EvaluationEvidenceBundle(BaseModel):
    scenario_id: str
    risk_id: str
    task_prompt: str
    task_spec: dict = Field(default_factory=dict)
    initial_plan: dict = Field(default_factory=dict)
    plan_revisions: list[dict] = Field(default_factory=list)
    execution_graph: dict = Field(default_factory=dict)
    registry_events: list[dict] = Field(default_factory=list)
    candidate_decisions: list[dict] = Field(default_factory=list)
    authorization_events: list[dict] = Field(default_factory=list)
    protocol_events: list[dict] = Field(default_factory=list)
    delegation_chain: list[dict] = Field(default_factory=list)
    agent_actions: list[dict] = Field(default_factory=list)
    tool_calls: list[dict] = Field(default_factory=list)
    artifacts: list[dict] = Field(default_factory=list)
    knowledge_events: list[dict] = Field(default_factory=list)
    human_events: list[dict] = Field(default_factory=list)
    deterministic_metrics: dict = Field(default_factory=dict)
    final_result: dict = Field(default_factory=dict)
