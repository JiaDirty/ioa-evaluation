"""External registry mutation surface used by realism-oriented risk tests.

The benchmark should not modify registry internals directly when modelling an
outside attacker. This module represents the public-facing mutation boundary:
agent providers can submit registration/update requests, while privileged
fields such as reputation and status require registry-admin authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..audit.audit_logger import AuditLogger
from ..core.data_models import AgentCard, AuditAction
from ..registry.registry import Registry


@dataclass
class RegistryMutationRequest:
    operation: str
    actor_id: str
    sub_ioa_id: str
    agent_id: str | None = None
    card: AgentCard | None = None
    updates: dict[str, Any] = field(default_factory=dict)
    actor_role: str = "external_provider"
    proof: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegistryMutationOutcome:
    operation: str
    applied: bool
    reason: str
    agent_id: str | None = None
    identity_verified: bool | None = None
    details: dict[str, Any] = field(default_factory=dict)


class RegistryAttackSurface:
    """Policy-checked external interface for registry mutation attempts."""

    PRIVILEGED_FIELDS = {"reputation_score", "status", "permission_scope"}

    def __init__(
        self,
        local_registry: Registry,
        global_registry: Registry,
        audit_logger: AuditLogger | None = None,
        sub_ioa_id: str = "",
    ) -> None:
        self.local_registry = local_registry
        self.global_registry = global_registry
        self.audit_logger = audit_logger
        self.sub_ioa_id = sub_ioa_id

    async def submit(self, request: RegistryMutationRequest) -> RegistryMutationOutcome:
        if request.sub_ioa_id != self.sub_ioa_id:
            return await self._outcome(
                request,
                applied=False,
                reason="request sub_ioa_id does not match this registry surface",
            )
        if request.operation == "register_agent":
            return await self._register_agent(request)
        if request.operation == "update_capabilities":
            return await self._update_capabilities(request)
        if request.operation in {"update_reputation", "update_status"}:
            return await self._privileged_update(request)
        return await self._outcome(request, applied=False, reason="unsupported registry operation")

    async def _register_agent(self, request: RegistryMutationRequest) -> RegistryMutationOutcome:
        if request.card is None:
            return await self._outcome(request, applied=False, reason="missing AgentCard")
        card = request.card.model_copy(deep=True)
        if card.sub_ioa_id != self.sub_ioa_id:
            return await self._outcome(request, applied=False, reason="AgentCard sub_ioa_id mismatch")
        if card.agent_id in {a.agent_id for a in await self.local_registry.list_agents(card.sub_ioa_id)}:
            return await self._outcome(request, applied=False, reason="agent already registered")

        await self.local_registry.register(card)
        await self.global_registry.register(card)
        verification = await self.local_registry.verify_identity(card.agent_id)
        reason = (
            "external registration accepted but identity remains unverified"
            if not verification.verified
            else "external registration accepted and identity verified"
        )
        return await self._outcome(
            request,
            applied=True,
            reason=reason,
            agent_id=card.agent_id,
            identity_verified=verification.verified,
            details={
                "certificate_valid": verification.certificate_valid,
                "gateway_verification_required": True,
            },
        )

    async def _update_capabilities(self, request: RegistryMutationRequest) -> RegistryMutationOutcome:
        if not request.agent_id:
            return await self._outcome(request, applied=False, reason="missing agent_id")
        agent = await self.local_registry.get_agent(request.agent_id)
        if agent is None:
            return await self._outcome(request, applied=False, reason="agent not found")
        if not self._has_owner_proof(request, agent):
            return await self._outcome(
                request,
                applied=False,
                reason="capability update rejected: missing provider ownership proof",
                agent_id=request.agent_id,
            )
        updates = {
            "declared_capabilities": list(request.updates.get("declared_capabilities", [])),
        }
        await self.local_registry.update(request.agent_id, updates)
        await self.global_registry.update(request.agent_id, updates)
        return await self._outcome(
            request,
            applied=True,
            reason="capability claim updated through authenticated external request",
            agent_id=request.agent_id,
        )

    async def _privileged_update(self, request: RegistryMutationRequest) -> RegistryMutationOutcome:
        if not request.agent_id:
            return await self._outcome(request, applied=False, reason="missing agent_id")
        if not self._is_registry_admin(request):
            return await self._outcome(
                request,
                applied=False,
                reason=f"{request.operation} rejected: registry-admin authorization required",
                agent_id=request.agent_id,
            )
        allowed = {
            key: value
            for key, value in request.updates.items()
            if key in self.PRIVILEGED_FIELDS
        }
        await self.local_registry.update(request.agent_id, allowed)
        await self.global_registry.update(request.agent_id, allowed)
        return await self._outcome(
            request,
            applied=True,
            reason=f"{request.operation} applied by registry admin",
            agent_id=request.agent_id,
        )

    def _has_owner_proof(self, request: RegistryMutationRequest, agent: AgentCard) -> bool:
        return (
            request.proof.get("provider") == agent.provider
            and request.proof.get("agent_id") == agent.agent_id
            and bool(request.proof.get("signed_nonce"))
        )

    def _is_registry_admin(self, request: RegistryMutationRequest) -> bool:
        expected = f"admin::{self.local_registry.registry_id}"
        return (
            request.actor_role == "registry_admin"
            and request.proof.get("admin_token") == expected
        )

    async def _outcome(
        self,
        request: RegistryMutationRequest,
        applied: bool,
        reason: str,
        agent_id: str | None = None,
        identity_verified: bool | None = None,
        details: dict[str, Any] | None = None,
    ) -> RegistryMutationOutcome:
        payload = {
            "operation": request.operation,
            "actor_id": request.actor_id,
            "actor_role": request.actor_role,
            "applied": applied,
            "reason": reason,
            "agent_id": agent_id or request.agent_id,
            **(details or {}),
        }
        if self.audit_logger is not None:
            await self.audit_logger.log_action(
                trace_id=f"registry-{request.operation}-{agent_id or request.agent_id or 'unknown'}",
                action=AuditAction.REGISTER,
                agent_id=request.actor_id,
                sub_ioa_id=request.sub_ioa_id,
                details={"stage": "external_registry_mutation", **payload},
            )
        return RegistryMutationOutcome(
            operation=request.operation,
            applied=applied,
            reason=reason,
            agent_id=agent_id or request.agent_id,
            identity_verified=identity_verified,
            details=payload,
        )
