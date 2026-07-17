"""Controlled dynamic delegation for agentic execution."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from pydantic import BaseModel, Field

from ..core.data_models import CapabilityRequirement


class DelegationRequest(BaseModel):
    parent_task_id: str
    parent_node_id: str
    requester_agent_id: str
    objective: str
    required_capabilities: list[CapabilityRequirement] = Field(default_factory=list)
    requested_scopes: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    expected_output: str = ""
    reason: str = ""
    preferred_agent_id: str | None = None


class DelegationGrant(BaseModel):
    delegation_id: str = Field(default_factory=lambda: f"delegation-{uuid.uuid4().hex[:8]}")
    parent_delegation_id: str | None = None
    grant_id: str = ""
    parent_grant_id: str | None = None
    root_task_id: str
    depth: int = 0
    requester_agent_id: str
    subject_agent_id: str = ""
    target_agent_id: str = ""
    resource_scope: list[str] = Field(default_factory=list)
    action_scope: list[str] = Field(default_factory=list)
    purpose: str = ""
    redelegation_allowed: bool = False
    effective_scopes: list[str] = Field(default_factory=list)
    allowed_data_classes: list[str] = Field(default_factory=list)
    expires_at: datetime = Field(default_factory=lambda: datetime.now() + timedelta(minutes=30))
    policy_ticket_id: str = ""

    def model_post_init(self, __context) -> None:
        if not self.grant_id:
            self.grant_id = self.delegation_id
        if self.parent_grant_id is None and self.parent_delegation_id is not None:
            self.parent_grant_id = self.parent_delegation_id
        if not self.subject_agent_id:
            self.subject_agent_id = self.target_agent_id or self.requester_agent_id
        if not self.action_scope:
            self.action_scope = list(self.effective_scopes)
        if not self.resource_scope:
            self.resource_scope = list(self.effective_scopes)


class DelegationDecision(BaseModel):
    allowed: bool
    grant: DelegationGrant | None = None
    reason: str = ""
    scope_expansion_detected: bool = False


class DelegationController:
    def __init__(self) -> None:
        self._grants: dict[str, DelegationGrant] = {}
        self._edges: set[tuple[str, str]] = set()

    def evaluate_request(
        self,
        request: DelegationRequest,
        *,
        parent_grant: DelegationGrant | None,
        user_scopes: list[str],
        policy_scopes: list[str],
        target_min_scopes: list[str] | None = None,
        target_agent_id: str = "",
        max_depth: int = 2,
    ) -> DelegationDecision:
        parent_scopes = (
            parent_grant.effective_scopes
            if parent_grant is not None
            else list(user_scopes)
        )
        depth = (parent_grant.depth + 1) if parent_grant is not None else 1
        if depth > max_depth:
            return DelegationDecision(allowed=False, reason="delegation depth limit exceeded")
        if target_agent_id and (target_agent_id, request.requester_agent_id) in self._edges:
            return DelegationDecision(allowed=False, reason="delegation cycle detected")

        requested = set(request.requested_scopes or parent_scopes)
        effective = (
            set(parent_scopes)
            & set(user_scopes)
            & requested
            & set(policy_scopes or user_scopes)
        )
        if target_min_scopes:
            effective &= set(target_min_scopes)
        expansion = bool(requested - set(parent_scopes))
        if expansion:
            return DelegationDecision(
                allowed=False,
                reason="delegation scope expansion denied",
                scope_expansion_detected=True,
            )
        grant = DelegationGrant(
            parent_delegation_id=parent_grant.delegation_id if parent_grant else None,
            parent_grant_id=parent_grant.grant_id if parent_grant else None,
            root_task_id=parent_grant.root_task_id if parent_grant else request.parent_task_id,
            depth=depth,
            requester_agent_id=request.requester_agent_id,
            target_agent_id=target_agent_id,
            subject_agent_id=target_agent_id or request.requester_agent_id,
            action_scope=sorted(effective),
            resource_scope=sorted(effective),
            purpose=request.objective,
            redelegation_allowed=depth < max_depth,
            effective_scopes=sorted(effective),
            policy_ticket_id=f"policy-{uuid.uuid4().hex[:8]}",
        )
        self._grants[grant.delegation_id] = grant
        if target_agent_id:
            self._edges.add((request.requester_agent_id, target_agent_id))
        return DelegationDecision(allowed=True, grant=grant, reason="delegation allowed")

    def get_grant(self, delegation_id: str) -> DelegationGrant | None:
        return self._grants.get(delegation_id)
