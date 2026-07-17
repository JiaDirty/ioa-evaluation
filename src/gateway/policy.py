"""Gateway authorization policy engine.

The gateway uses this module as a small RBAC/ABAC policy engine instead of
mixing all authorization logic into string-scope checks. It is intentionally
deterministic so benchmark runs remain reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from ..core.data_models import AgentCard, AuthResult, Task


@dataclass(frozen=True)
class RuleEvaluation:
    """A single policy rule decision."""

    rule: str
    allowed: bool
    reason: str = ""


@dataclass(frozen=True)
class PolicySubject:
    """Attributes about the requesting principal."""

    requester_id: str
    granted_scope: list[str]
    sub_ioa_id: str = ""
    provider: str = ""
    reputation_score: float = 0.0
    is_user: bool = False


@dataclass(frozen=True)
class PolicyDecision:
    """Full policy decision with explainable rule evidence."""

    authorized: bool
    granted_scope: list[str]
    evaluations: list[RuleEvaluation] = field(default_factory=list)

    @property
    def reason(self) -> str:
        failures = [ev.reason for ev in self.evaluations if not ev.allowed and ev.reason]
        return "; ".join(failures)


@dataclass(frozen=True)
class PolicyConfig:
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def default(cls) -> "PolicyConfig":
        return cls({
            "global": {
                "max_delegation_depth": 2,
                "require_audit": True,
                "default_min_protocol_security_level": "medium",
            },
            "protocols": {
                "a2a": {"enabled": True},
                "mcp": {"enabled": True},
                "private_api": {"enabled": True},
            },
            "actions": {},
            "data_classes": {},
            "tools": {"high_risk_requires_scope": "high_risk_tool"},
        })

    @classmethod
    def load(cls, path: str = "config/policies.yaml") -> "PolicyConfig":
        config_path = Path(path)
        if not config_path.exists():
            return cls.default()
        with config_path.open("r", encoding="utf-8") as fh:
            loaded = yaml.safe_load(fh) or {}
        default = cls.default().raw
        merged = {**default, **loaded}
        for key in ("global", "protocols", "actions", "data_classes", "tools"):
            merged[key] = {**default.get(key, {}), **loaded.get(key, {})}
        return cls(merged)

    @property
    def max_delegation_depth(self) -> int:
        return int(self.raw.get("global", {}).get("max_delegation_depth", 2))

    def action(self, name: str) -> dict[str, Any]:
        return dict(self.raw.get("actions", {}).get(name, {}))

    def data_class(self, name: str) -> dict[str, Any]:
        return dict(self.raw.get("data_classes", {}).get(name, {}))


class AuthorizationPolicyEngine:
    """Evaluate Gateway RBAC and ABAC authorization rules."""

    def __init__(self, config: PolicyConfig | None = None) -> None:
        self.config = config or PolicyConfig.load()

    def evaluate(
        self,
        subject: PolicySubject,
        task: Task,
        required_scope: list[str],
    ) -> PolicyDecision:
        evaluations = [
            self._evaluate_rbac(subject.granted_scope, required_scope, subject.requester_id),
            self._evaluate_min_reputation(subject, task),
            self._evaluate_allowed_sub_ioas(subject, task),
            self._evaluate_denied_sub_ioas(subject, task),
            self._evaluate_allowed_providers(subject, task),
            self._evaluate_delegation_depth(task),
            self._evaluate_configured_human_approval(task),
            self._evaluate_configured_citations(task),
        ]
        return PolicyDecision(
            authorized=all(ev.allowed for ev in evaluations),
            granted_scope=subject.granted_scope,
            evaluations=evaluations,
        )

    def _evaluate_delegation_depth(self, task: Task) -> RuleEvaluation:
        depth = int(task.payload.get("delegation_depth", 0))
        if depth > self.config.max_delegation_depth:
            return RuleEvaluation(
                rule="config_max_delegation_depth",
                allowed=False,
                reason=(
                    f"delegation_depth {depth} exceeds max "
                    f"{self.config.max_delegation_depth}"
                ),
            )
        return RuleEvaluation(rule="config_max_delegation_depth", allowed=True)

    def _evaluate_configured_human_approval(self, task: Task) -> RuleEvaluation:
        action_type = str(task.payload.get("action_type", ""))
        data_classes = [str(item) for item in task.payload.get("data_classes", [])]
        requires = False
        if action_type:
            requires = bool(self.config.action(action_type).get("require_human_approval", False))
        requires = requires or any(
            self.config.data_class(name).get("require_human_approval", False)
            for name in data_classes
        )
        if requires and not task.payload.get("human_approval_granted"):
            return RuleEvaluation(
                rule="config_human_approval",
                allowed=False,
                reason="configured policy requires human approval",
            )
        return RuleEvaluation(rule="config_human_approval", allowed=True)

    def _evaluate_configured_citations(self, task: Task) -> RuleEvaluation:
        action_type = str(task.payload.get("action_type", ""))
        action = self.config.action(action_type)
        if action.get("require_citations") and not (
            task.payload.get("require_citations") or task.constraints.require_citations
        ):
            return RuleEvaluation(
                rule="config_require_citations",
                allowed=False,
                reason=f"action {action_type} requires citations",
            )
        return RuleEvaluation(rule="config_require_citations", allowed=True)

    def _evaluate_rbac(
        self, granted_scope: list[str], required_scope: list[str], requester_id: str
    ) -> RuleEvaluation:
        missing = [
            scope for scope in required_scope
            if not self.scope_allows(granted_scope, scope)
        ]
        if missing:
            return RuleEvaluation(
                rule="rbac_scope",
                allowed=False,
                reason=(
                    f"Requester {requester_id} missing required scopes: "
                    f"{', '.join(missing)}"
                ),
            )
        return RuleEvaluation(rule="rbac_scope", allowed=True)

    @staticmethod
    def _evaluate_min_reputation(subject: PolicySubject, task: Task) -> RuleEvaluation:
        min_rep = task.payload.get("min_reputation")
        if min_rep is None or subject.is_user:
            return RuleEvaluation(rule="abac_min_reputation", allowed=True)
        if subject.reputation_score < float(min_rep):
            return RuleEvaluation(
                rule="abac_min_reputation",
                allowed=False,
                reason=(
                    f"Requester {subject.requester_id} below min_reputation "
                    f"{float(min_rep):.2f}: {subject.reputation_score:.2f}"
                ),
            )
        return RuleEvaluation(rule="abac_min_reputation", allowed=True)

    @staticmethod
    def _evaluate_allowed_sub_ioas(subject: PolicySubject, task: Task) -> RuleEvaluation:
        allowed = task.payload.get("allowed_requester_sub_ioas")
        if not allowed or subject.is_user:
            return RuleEvaluation(rule="abac_allowed_sub_ioas", allowed=True)
        if subject.sub_ioa_id not in set(allowed):
            return RuleEvaluation(
                rule="abac_allowed_sub_ioas",
                allowed=False,
                reason=(
                    f"Requester {subject.requester_id} not in "
                    f"allowed_requester_sub_ioas: {allowed}"
                ),
            )
        return RuleEvaluation(rule="abac_allowed_sub_ioas", allowed=True)

    @staticmethod
    def _evaluate_denied_sub_ioas(subject: PolicySubject, task: Task) -> RuleEvaluation:
        denied = set(task.payload.get("denied_requester_sub_ioas", []))
        if not denied or subject.is_user:
            return RuleEvaluation(rule="abac_denied_sub_ioas", allowed=True)
        if subject.sub_ioa_id in denied:
            return RuleEvaluation(
                rule="abac_denied_sub_ioas",
                allowed=False,
                reason=(
                    f"Requester {subject.requester_id} is in "
                    f"denied_requester_sub_ioas: {sorted(denied)}"
                ),
            )
        return RuleEvaluation(rule="abac_denied_sub_ioas", allowed=True)

    @staticmethod
    def _evaluate_allowed_providers(subject: PolicySubject, task: Task) -> RuleEvaluation:
        allowed = task.payload.get("allowed_requester_providers")
        if not allowed or subject.is_user:
            return RuleEvaluation(rule="abac_allowed_providers", allowed=True)
        if subject.provider not in set(allowed):
            return RuleEvaluation(
                rule="abac_allowed_providers",
                allowed=False,
                reason=(
                    f"Requester {subject.requester_id} provider not allowed: "
                    f"{subject.provider}"
                ),
            )
        return RuleEvaluation(rule="abac_allowed_providers", allowed=True)

    @staticmethod
    def scope_allows(granted_scope: list[str], required: str) -> bool:
        granted = set(granted_scope)
        if "*" in granted or required in granted:
            return True
        if required.startswith("read_") and "read" in granted:
            return True
        if required.startswith("write_") and "write" in granted:
            return True
        return False


def subject_from_agent(card: AgentCard, requester_id: str) -> PolicySubject:
    return PolicySubject(
        requester_id=requester_id,
        granted_scope=list(card.permission_scope),
        sub_ioa_id=card.sub_ioa_id,
        provider=card.provider,
        reputation_score=card.reputation_score,
    )


def subject_from_user(requester_id: str, attrs: dict[str, Any]) -> PolicySubject:
    return PolicySubject(
        requester_id=requester_id,
        granted_scope=list(attrs.get("requester_scope", ["submit", "read", "execute"])),
        sub_ioa_id=str(attrs.get("requester_sub_ioa_id", "user")),
        provider=str(attrs.get("requester_provider", "human")),
        reputation_score=float(attrs.get("requester_reputation", 1.0)),
        is_user=True,
    )


def auth_result_from_decision(decision: PolicyDecision) -> AuthResult:
    return AuthResult(
        authorized=decision.authorized,
        granted_scope=decision.granted_scope,
        reason=decision.reason,
    )
