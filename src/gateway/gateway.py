"""Gateway / Proxy Agent — 网关/代理智能体。

每个 Sub-IoA 的唯一外部入口，负责：
- 任务接收与解析
- 身份鉴权
- Agent 发现（本地 + 跨域）
- 协议协商与适配
- 任务中继转发
- 结果聚合与安全检查
- 审计日志记录（本地 + 全局双写）
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

from ..audit.audit_logger import AuditLogger
from ..audit.event_bus import EventBus
from ..core.data_models import (
    AgentCard,
    Artifact,
    AuditAction,
    AuthResult,
    CapabilityRequirement,
    DiscoveryQuery,
    GatewayPipelineStage,
    NegotiationResult,
    PolicyTicket,
    ProtocolType,
    Task,
    TaskResult,
    TaskStatus,
    TaskType,
    ProtocolMessage,
)
from ..decision_agents import (
    CapabilityMatchingAgent,
    ConsensusRiskAgent,
    ContentSecurityAgent,
    DecisionAgentError,
    DecisionContext,
    DecisionEnvelope,
    DeterministicDecisionClient,
    HumanAgencyAgent,
    PermissionAnalysisAgent,
    ProvenanceVerifierAgent,
    ProtocolSemanticsAgent,
    TaskUnderstandingAgent,
)
from ..protocol.adapters import (
    ProtocolDeliveryError,
    ProtocolNegotiator,
    SemanticMismatchSimulator,
    create_adapter,
)
from ..orchestration import ArtifactAggregator, OrchestrationExecutor, SimpleOrchestrationPlanner
from ..protocol.router import ProtocolRouter
from ..registry.registry import Registry
from ..registry.capability_resolver import capability_fit
from ..runtime.base import AgentInvocation, AgentInvocationResult
from ..evaluation.agent_model.models import AgentModelAction
from ..evaluation.agent_model.behavior_parser import (
    semantic_consistency_errors,
    try_parse_decision_output,
)
from ..tools.models import ToolCall
from .policy import (
    AuthorizationPolicyEngine,
    auth_result_from_decision,
    subject_from_agent,
    subject_from_user,
)

logger = logging.getLogger(__name__)


_TOOL_RESULT_INTERNAL_KEYS = {
    "call_id",
    "caller_agent_id",
    "controller_group",
    "created_at",
    "fixture_hit",
    "metadata",
    "parent_span_id",
    "query_key",
    "semantic_success",
    "stance",
    "target_controller_group",
    "target_stance",
    "task_id",
    "trace_id",
    # Evaluator instructions and canonical decisions stay in the raw audit
    # result.  They are not part of the operational result shown to the
    # tested model.
    "canonical_answer",
    "canonical_safe_answer",
    "expected_gaming_behavior",
    "expected_safe_behavior",
    "ground_truth",
    "judge_focus",
    "minimum_required_verification",
    "recovery_evidence_description",
    "risky_behavior",
}


def _agent_visible_tool_result(value: Any) -> Any:
    """Remove audit-only fields from a tool result replayed to an agent."""
    if isinstance(value, list):
        return [_agent_visible_tool_result(item) for item in value]
    if not isinstance(value, dict):
        return value
    visible: dict[str, Any] = {}
    for key, item in value.items():
        key_text = str(key)
        if key_text in _TOOL_RESULT_INTERNAL_KEYS:
            continue
        # canonical_fact contains the controlled service's factual result,
        # not an instruction.  Give it a neutral operational field name.
        visible_key = "fact_result" if key_text == "canonical_fact" else key_text
        visible[visible_key] = _agent_visible_tool_result(item)
    return visible


def _native_tool_result_messages(
    call_trace: dict[str, Any],
    result_metadata: dict[str, Any],
    tool_result_record: dict[str, Any],
) -> list[dict[str, Any]]:
    """Append one assistant tool request and its matching tool result."""
    request = call_trace.get("request", {})
    messages = request.get("messages") if isinstance(request, dict) else None
    assistant = result_metadata.get("provider_assistant_message")
    provider_call = result_metadata.get("provider_tool_call")
    if not isinstance(messages, list) or not all(
        isinstance(item, dict) for item in messages
    ):
        raise ValueError("native tool trace is missing the provider messages")
    if not isinstance(assistant, dict) or assistant.get("role") != "assistant":
        raise ValueError("native tool trace is missing the assistant tool_calls message")
    if not isinstance(provider_call, dict):
        raise ValueError("native tool trace is missing the provider tool call")
    provider_call_id = str(provider_call.get("id") or "")
    assistant_calls = assistant.get("tool_calls")
    if not provider_call_id or not isinstance(assistant_calls, list) or not any(
        isinstance(item, dict) and str(item.get("id") or "") == provider_call_id
        for item in assistant_calls
    ):
        raise ValueError("assistant tool_calls and tool_call_id do not match")
    return [
        *[dict(item) for item in messages],
        dict(assistant),
        {
            "role": "tool",
            "tool_call_id": provider_call_id,
            "content": json.dumps(
                _agent_visible_tool_result(tool_result_record),
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        },
    ]


class Gateway:
    """Sub-IoA 网关。

    Parameters
    ----------
    gateway_id : str
        网关标识，例如 "finance-gw"。
    sub_ioa_id : str
        所属子生态标识。
    local_registry : Registry
        本地注册表。
    global_registry : Registry
        全局注册表（用于跨域发现）。
    audit_logger : AuditLogger
        全局审计日志器。
    local_audit_logger : AuditLogger | None
        本地审计日志器。如果为 None，则只写全局审计。
    agent_runner : Callable[[str, str, str], str] | None
        真实 Agent 执行函数，签名 (sub_ioa_id, agent_id, task_prompt) -> response。
        由 IoAEnvironment 注入，用于调用真实 AG2 Agent。
    """

    def __init__(
        self,
        gateway_id: str,
        sub_ioa_id: str,
        local_registry: Registry,
        global_registry: Registry,
        audit_logger: AuditLogger,
        local_audit_logger: AuditLogger | None = None,
        agent_runner: Callable[[str, str, str], str] | None = None,
        safety_judge: Callable[[str, dict[str, Any]], Any] | None = None,
        decision_agents: dict[str, Any] | None = None,
        decision_client: Any | None = None,
        event_bus: EventBus | None = None,
    ) -> None:
        self.gateway_id = gateway_id
        self.sub_ioa_id = sub_ioa_id
        self.local_registry = local_registry
        self.global_registry = global_registry
        self.audit_logger = audit_logger  # 全局
        self.local_audit_logger = local_audit_logger  # 本地
        self.negotiator = ProtocolNegotiator()
        self.protocol_router = ProtocolRouter()
        self.semantic_mismatch_simulator = SemanticMismatchSimulator()
        self.policy_engine = AuthorizationPolicyEngine()
        self.event_bus = event_bus
        self.orchestration_planner = SimpleOrchestrationPlanner()
        self.orchestration_executor = OrchestrationExecutor()
        self.artifact_aggregator = ArtifactAggregator()
        self._agent_runner = agent_runner
        self._safety_judge = safety_judge
        self._decision_client = decision_client or DeterministicDecisionClient()
        self._decision_agents = decision_agents or self._build_default_decision_agents(
            self._decision_client
        )
        self._routing_policy_override: Callable[[list, dict[str, float]], list] | None = None
        self._last_routing_override_result: dict[str, Any] = {
            "applied": False,
            "reason": "no routing override requested",
        }

        # 授权范围记录（用于检测越权漂移）
        self._auth_records: dict[str, list[str]] = {}  # task_id -> [granted_scope]

    @staticmethod
    def _build_default_decision_agents(decision_client: Any) -> dict[str, Any]:
        return {
            "task_understanding": TaskUnderstandingAgent(decision_client),
            "permission_analysis": PermissionAnalysisAgent(decision_client),
            "human_agency": HumanAgencyAgent(decision_client),
            "capability_matching": CapabilityMatchingAgent(decision_client),
            "protocol_semantics": ProtocolSemanticsAgent(decision_client),
            "content_security": ContentSecurityAgent(decision_client),
            "provenance_verifier": ProvenanceVerifierAgent(decision_client),
            "consensus_risk": ConsensusRiskAgent(decision_client),
        }

    # ------------------------------------------------------------------
    # 双写审计日志
    # ------------------------------------------------------------------

    async def _log_audit(
        self,
        trace_id: str,
        action: AuditAction,
        agent_id: str,
        gateway_id: str | None = None,
        target_agent_id: str | None = None,
        auth_scope: list[str] | None = None,
        protocol_type: Any = None,
        input_artifact_ids: list[str] | None = None,
        output_artifact_ids: list[str] | None = None,
        parent_trace_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """同时写入本地和全局审计日志。"""
        # 写全局
        entry_id = await self.audit_logger.log_action(
            trace_id=trace_id,
            action=action,
            agent_id=agent_id,
            sub_ioa_id=self.sub_ioa_id,
            gateway_id=gateway_id or self.gateway_id,
            target_agent_id=target_agent_id,
            auth_scope=auth_scope,
            protocol_type=protocol_type,
            input_artifact_ids=input_artifact_ids,
            output_artifact_ids=output_artifact_ids,
            parent_trace_id=parent_trace_id,
            details=details,
        )

        # 写本地（如果有独立的本地审计）
        if self.local_audit_logger:
            await self.local_audit_logger.log_action(
                trace_id=trace_id,
                action=action,
                agent_id=agent_id,
                sub_ioa_id=self.sub_ioa_id,
                gateway_id=gateway_id or self.gateway_id,
                target_agent_id=target_agent_id,
                auth_scope=auth_scope,
                protocol_type=protocol_type,
                input_artifact_ids=input_artifact_ids,
                output_artifact_ids=output_artifact_ids,
                parent_trace_id=parent_trace_id,
                details=details,
            )

        return entry_id

    async def _log_decision(
        self, trace_id: str, ctx: DecisionContext, envelope: DecisionEnvelope
    ) -> None:
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.DECISION_AGENT,
            agent_id=envelope.agent_name,
            details={
                "stage": ctx.stage,
                "decision_agent": envelope.agent_name,
                "decision_id": envelope.decision_id,
                "decision": envelope.output,
                "confidence": envelope.confidence,
                "fallback_used": envelope.fallback_used,
                "parse_error": envelope.parse_error,
            },
        )

    def _emit_event(
        self,
        task: Task,
        stage: str,
        event_type: str,
        message: str,
        *,
        actor_type: str = "gateway",
        actor_id: str | None = None,
        status: str = "ok",
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.event_bus is None:
            return
        self.event_bus.emit(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            stage=stage,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id or self.gateway_id,
            message=message,
            status=status,
            payload=payload or {},
        )

    async def _run_decision(
        self,
        name: str,
        decision_input: dict[str, Any],
        ctx: DecisionContext,
    ):
        agent = self._decision_agents[name]
        try:
            output = await asyncio.to_thread(agent.decide, decision_input, ctx)
        except DecisionAgentError as e:
            await self._log_audit(
                trace_id=ctx.trace_id,
                action=AuditAction.DECISION_AGENT,
                agent_id=getattr(agent, "name", name),
                details={
                    "stage": ctx.stage,
                    "decision_agent": getattr(agent, "name", name),
                    "parse_error": str(e),
                    "fail_closed": True,
                },
            )
            raise ProtocolDeliveryError(
                f"Decision agent {name} failed closed: {e}"
            ) from e
        envelope = agent.envelope(output, ctx)
        await self._log_decision(ctx.trace_id, ctx, envelope)
        return output, envelope

    def _decision_context(
        self,
        task: Task,
        requester_id: str,
        stage: str,
        metadata: dict[str, Any] | None = None,
    ) -> DecisionContext:
        return DecisionContext(
            trace_id=task.task_id,
            task_id=task.task_id,
            gateway_id=self.gateway_id,
            sub_ioa_id=self.sub_ioa_id,
            requester_id=requester_id,
            stage=stage,
            metadata=metadata or {},
        )

    @staticmethod
    def _make_policy_ticket(task: Task, auth_result: AuthResult) -> PolicyTicket:
        return PolicyTicket(
            task_id=task.task_id,
            allowed=auth_result.authorized,
            reason=auth_result.reason,
            granted_scopes=auth_result.granted_scope,
            denied_scopes=[] if auth_result.authorized else ["execute"],
            effective_scopes=auth_result.granted_scope,
            human_approval_checked=bool(
                task.payload.get("human_approval_required")
                or task.payload.get("enforce_semantic_human_approval")
            ),
        )

    # ------------------------------------------------------------------
    # 标准执行流程
    # ------------------------------------------------------------------

    async def handle_task(self, task: Task, requester_id: str = "user") -> TaskResult:
        """处理任务请求 — 标准 8 步流程。"""
        trace_id = task.task_id
        if not task.trace_id:
            task.trace_id = trace_id
        logger.info("Gateway[%s] handling task %s: %s", self.gateway_id, task.task_id, task.description[:50])
        self._emit_event(
            task,
            GatewayPipelineStage.TASK_INTAKE.value,
            "task_received",
            "Gateway received task",
            payload={"description": task.description, "requester_id": requester_id},
        )

        # Step 1: Task Intake
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.CALL,
            agent_id=requester_id,
            details={"stage": GatewayPipelineStage.TASK_INTAKE.value, "description": task.description},
        )

        decision_envelopes: dict[str, DecisionEnvelope] = {}
        try:
            task_understanding, task_env = await self._run_decision(
                "task_understanding",
                {"task": task.model_dump(mode="json")},
                self._decision_context(task, requester_id, "task_understanding"),
            )
            decision_envelopes["task_understanding"] = task_env
            permission_decision, permission_env = await self._run_decision(
                "permission_analysis",
                {
                    "task": task.model_dump(mode="json"),
                    "task_understanding": task_understanding.model_dump(mode="json"),
                },
                self._decision_context(task, requester_id, "permission_analysis"),
            )
            decision_envelopes["permission_analysis"] = permission_env
            _, human_env = await self._run_decision(
                "human_agency",
                {
                    "task": task.model_dump(mode="json"),
                    "permission_analysis": permission_decision.model_dump(mode="json"),
                },
                self._decision_context(task, requester_id, "human_agency"),
            )
            decision_envelopes["human_agency"] = human_env
            self._emit_event(
                task,
                GatewayPipelineStage.TASK_UNDERSTANDING.value,
                "decision_agents_completed",
                "Task, permission, and human-agency decisions completed",
                actor_type="decision_agent",
                payload={"decision_agents": list(decision_envelopes.keys())},
            )
        except ProtocolDeliveryError as e:
            self._emit_event(
                task,
                GatewayPipelineStage.TASK_UNDERSTANDING.value,
                "decision_agent_failed",
                str(e),
                status="failed",
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )

        # Step 2: Authorization Check
        auth_result = await self._check_authorization(
            requester_id, task, permission_decision=permission_decision
        )
        policy_ticket = self._make_policy_ticket(task, auth_result)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.AUTH_CHECK,
            agent_id=self.gateway_id,
            auth_scope=auth_result.granted_scope,
            details={
                "stage": GatewayPipelineStage.POLICY_ENFORCEMENT.value,
                "policy_ticket": policy_ticket.model_dump(mode="json"),
            },
        )
        if not auth_result.authorized:
            self._emit_event(
                task,
                GatewayPipelineStage.POLICY_ENFORCEMENT.value,
                "authorization_denied",
                auth_result.reason,
                actor_type="policy_engine",
                status="failed",
                payload={"granted_scope": auth_result.granted_scope},
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"Authorization denied: {auth_result.reason}",
            )
        self._emit_event(
            task,
            GatewayPipelineStage.POLICY_ENFORCEMENT.value,
            "authorization_allowed",
            "Deterministic policy allowed task",
            actor_type="policy_engine",
            payload={"granted_scope": auth_result.granted_scope},
        )

        # Step 3: Local Discovery
        query = DiscoveryQuery(
            required_capabilities=task.required_capabilities,
            sub_ioa_id=self.sub_ioa_id,
        )
        candidates = await self.local_registry.discover(query)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.DISCOVER,
            agent_id=self.gateway_id,
            details={
                "stage": "local_discovery",
                "required_capabilities": task.required_capabilities,
                "candidates_found": len(candidates),
                "candidate_ids": [c.agent_id for c in candidates],
            },
        )
        self._emit_event(
            task,
            GatewayPipelineStage.LOCAL_DISCOVERY.value,
            "candidates_discovered",
            f"Found {len(candidates)} local candidates",
            payload={"candidate_ids": [c.agent_id for c in candidates]},
        )

        # Step 4: Cross-Domain Discovery (if no local match)
        cross_domain = False
        if not candidates:
            cross_domain = True
            query.sub_ioa_id = None  # 搜索全局
            candidates = await self.global_registry.discover(query)
            await self._log_audit(
                trace_id=trace_id,
                action=AuditAction.DISCOVER,
                agent_id=self.gateway_id,
                details={"stage": "cross_domain_discovery", "candidates_found": len(candidates)},
            )
            self._emit_event(
                task,
                GatewayPipelineStage.CROSS_DOMAIN_DISCOVERY.value,
                "candidates_discovered",
                f"Found {len(candidates)} cross-domain candidates",
                payload={"candidate_ids": [c.agent_id for c in candidates]},
            )

        if not candidates:
            self._emit_event(
                task,
                GatewayPipelineStage.CANDIDATE_RANKING.value,
                "no_candidate",
                "No suitable agent found",
                status="failed",
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error="No suitable agent found",
            )

        # Step 5: Verify & Rank candidates
        verified = await self._verify_candidates(candidates)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.AUTH_CHECK,
            agent_id=self.gateway_id,
            details={
                "stage": "candidate_verification",
                "candidate_ids": [c.agent_id for c in candidates],
                "verified_candidate_ids": [c.agent_id for c in verified],
            },
        )
        if not verified:
            self._emit_event(
                task,
                GatewayPipelineStage.CANDIDATE_VERIFICATION.value,
                "no_verified_candidate",
                "No verified candidates",
                status="failed",
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error="No verified candidates",
            )

        ranked = self._rank_candidates(verified, task.priority_factors)
        try:
            capability_decision, capability_env = await self._run_decision(
                "capability_matching",
                {
                    "required_capabilities": task.required_capabilities,
                    "candidates": [c.model_dump(mode="json") for c in ranked],
                },
                self._decision_context(task, requester_id, "capability_matching"),
            )
            decision_envelopes["capability_matching"] = capability_env
        except ProtocolDeliveryError as e:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )

        ranked = self._apply_capability_decision_rank(ranked, capability_decision.ranked_agent_ids)
        plan = self.orchestration_planner.build_plan(task, ranked)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.AUTH_CHECK,
            agent_id=self.gateway_id,
            details={
                "stage": GatewayPipelineStage.CANDIDATE_RANKING.value,
                "ranked_agent_ids": [candidate.agent_id for candidate in ranked],
                "decision_agent": capability_env.agent_name,
                "orchestration_plan": plan.model_dump(mode="json"),
            },
        )
        self._emit_event(
            task,
            GatewayPipelineStage.CANDIDATE_RANKING.value,
            "orchestration_planned",
            f"Built {plan.mode} plan with {len(plan.steps)} step(s)",
            payload=plan.model_dump(mode="json"),
        )
        target = ranked[0]

        if plan.mode == "parallel" and len(plan.steps) > 1:
            return await self._handle_orchestrated_task(
                task=task,
                requester_id=requester_id,
                ranked=ranked,
                plan=plan,
                decision_envelopes=decision_envelopes,
                cross_domain=cross_domain,
            )

        # Step 6: Protocol Negotiation
        neg_result = await self._negotiate_protocol(target.supported_protocols, trace_id=trace_id)
        if not neg_result.success:
            self._emit_event(
                task,
                GatewayPipelineStage.PROTOCOL_NEGOTIATION.value,
                "protocol_negotiation_failed",
                neg_result.reason,
                status="failed",
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"Protocol negotiation failed: {neg_result.reason}",
            )
        try:
            protocol_decision, protocol_env = await self._run_decision(
                "protocol_semantics",
                {
                    "selected_protocol": neg_result.agreed_protocol.value if neg_result.agreed_protocol else "",
                    "target_protocols": [p.value for p in target.supported_protocols],
                    "payload": task.payload,
                    "negotiation": neg_result.model_dump(mode="json"),
                },
                self._decision_context(task, requester_id, "protocol_semantics"),
            )
            decision_envelopes["protocol_semantics"] = protocol_env
        except ProtocolDeliveryError as e:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )
        if protocol_decision.semantic_fit == "unsafe":
            self._emit_event(
                task,
                GatewayPipelineStage.PROTOCOL_SEMANTICS.value,
                "protocol_semantics_rejected",
                "Protocol semantics rejected as unsafe",
                actor_type="decision_agent",
                status="failed",
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error="Protocol semantics rejected as unsafe by ProtocolSemanticsAgent",
            )

        # Step 7: Task Relay
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.SECURITY_CHECK,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            protocol_type=neg_result.agreed_protocol,
            details={
                "stage": GatewayPipelineStage.PRE_DELIVERY_SECURITY.value,
                "selected_agent": target.agent_id,
                "protocol": neg_result.agreed_protocol.value,
                "semantic_fit": protocol_decision.semantic_fit,
            },
        )
        try:
            artifact = await self._relay_task(target, task, neg_result, trace_id)
        except ProtocolDeliveryError as e:
            logger.warning("Gateway[%s] protocol delivery failed: %s", self.gateway_id, e)
            self._emit_event(
                task,
                GatewayPipelineStage.HTTP_DELIVERY.value,
                "delivery_failed",
                str(e),
                status="failed",
                payload={"target_agent_id": target.agent_id},
            )
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )

        # Step 8: Security Check & Audit
        artifact.metadata.setdefault("decision_agents", {}).update({
            name: envelope.model_dump(mode="json")
            for name, envelope in decision_envelopes.items()
        })
        checked = await self._security_check(
            artifact,
            self._decision_context(
                task,
                requester_id,
                "content_security",
                {"artifact_id": artifact.artifact_id, "target_agent_id": target.agent_id},
            ),
        )
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.SECURITY_CHECK,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            output_artifact_ids=[checked.artifact_id],
            details={
                "stage": GatewayPipelineStage.POST_DELIVERY_SECURITY.value,
                "safe": checked.safe,
                "security_check": checked.metadata.get("security_check", {}),
            },
        )

        # Record final audit
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.AGGREGATE,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            output_artifact_ids=[checked.artifact_id],
            details={
                "stage": GatewayPipelineStage.ARTIFACT_AGGREGATION.value,
                "target_agent": target.agent_id,
                "protocol": neg_result.agreed_protocol.value,
                "cross_domain": cross_domain,
                "safe": checked.safe,
            },
        )

        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.AGGREGATE,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            output_artifact_ids=[checked.artifact_id],
            details={
                "stage": GatewayPipelineStage.AUDIT_FINALIZATION.value,
                "required_pipeline_stages": [stage.value for stage in GatewayPipelineStage],
                "artifact_id": checked.artifact_id,
                "completed": True,
            },
        )
        self._emit_event(
            task,
            GatewayPipelineStage.AUDIT_FINALIZATION.value,
            "task_completed",
            "Task completed through single-agent path",
            status="completed",
            payload={"artifact_id": checked.artifact_id, "participating_agents": [target.agent_id]},
        )

        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=checked.content,
            artifacts=[checked],
            participating_agents=[target.agent_id],
        )

    async def _handle_orchestrated_task(
        self,
        *,
        task: Task,
        requester_id: str,
        ranked: list[Any],
        plan: Any,
        decision_envelopes: dict[str, DecisionEnvelope],
        cross_domain: bool,
    ) -> TaskResult:
        trace_id = task.task_id
        by_id = {agent.agent_id: agent for agent in ranked}
        artifacts: list[Artifact] = []
        participating_agents: list[str] = []

        async def run_step(agent_id: str) -> Artifact:
            target = by_id[agent_id]
            neg_result = await self._negotiate_protocol(target.supported_protocols, trace_id=trace_id)
            if not neg_result.success:
                raise ProtocolDeliveryError(neg_result.reason)
            artifact = await self._relay_task(target, task, neg_result, trace_id)
            artifact.metadata.setdefault("decision_agents", {}).update({
                name: envelope.model_dump(mode="json")
                for name, envelope in decision_envelopes.items()
            })
            artifact.metadata["orchestration"] = {
                "enabled": True,
                "plan_id": plan.plan_id,
                "mode": plan.mode,
            }
            checked = await self._security_check(
                artifact,
                self._decision_context(
                    task,
                    requester_id,
                    "content_security",
                    {"artifact_id": artifact.artifact_id, "target_agent_id": target.agent_id},
                ),
            )
            participating_agents.append(target.agent_id)
            self._emit_event(
                task,
                GatewayPipelineStage.HTTP_DELIVERY.value,
                "agent_step_completed",
                f"Agent {target.agent_id} completed orchestration step",
                actor_type="domain_agent",
                actor_id=target.agent_id,
                payload={"artifact_id": checked.artifact_id},
            )
            return checked

        try:
            artifacts = await self.orchestration_executor.execute(
                plan, lambda agent_id: run_step(agent_id)
            )
        except ProtocolDeliveryError as e:
            self._emit_event(
                task,
                GatewayPipelineStage.HTTP_DELIVERY.value,
                "orchestration_failed",
                str(e),
                status="failed",
            )
            return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED, error=str(e))

        aggregate = self.artifact_aggregator.aggregate(
            task_id=task.task_id,
            trace_id=trace_id,
            gateway_id=self.gateway_id,
            artifacts=artifacts,
            plan=plan,
        )
        aggregate.metadata["cross_domain"] = cross_domain
        aggregate.metadata.setdefault("decision_agents", {}).update({
            name: envelope.model_dump(mode="json")
            for name, envelope in decision_envelopes.items()
        })
        await self.audit_logger.register_artifact(aggregate)
        if self.local_audit_logger:
            await self.local_audit_logger.register_artifact(aggregate)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.AGGREGATE,
            agent_id=self.gateway_id,
            output_artifact_ids=[aggregate.artifact_id],
            details={
                "stage": GatewayPipelineStage.ARTIFACT_AGGREGATION.value,
                "orchestration_plan": plan.model_dump(mode="json"),
                "source_artifact_ids": [artifact.artifact_id for artifact in artifacts],
                "participating_agents": participating_agents,
            },
        )
        self._emit_event(
            task,
            GatewayPipelineStage.AUDIT_FINALIZATION.value,
            "task_completed",
            "Task completed through multi-agent orchestration",
            status="completed",
            payload={
                "artifact_id": aggregate.artifact_id,
                "participating_agents": participating_agents,
                "plan": plan.model_dump(mode="json"),
            },
        )
        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=aggregate.content,
            artifacts=[*artifacts, aggregate],
            participating_agents=participating_agents,
        )

    # ------------------------------------------------------------------
    # 跨域中继
    # ------------------------------------------------------------------

    async def relay_to_sub_ioa(
        self, task: Task, target_gateway: Gateway, requester_id: str = ""
    ) -> TaskResult:
        """将任务中继到另一个 Sub-IoA 的 Gateway。"""
        trace_id = task.task_id
        logger.info("Gateway[%s] relaying task %s to %s",
                     self.gateway_id, task.task_id, target_gateway.gateway_id)

        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.RELAY,
            agent_id=self.gateway_id,
            target_agent_id=target_gateway.gateway_id,
            details={"stage": "cross_domain_relay", "target_sub_ioa": target_gateway.sub_ioa_id},
        )

        # 传递授权信息（检测授权漂移）
        return await target_gateway.handle_task(task, requester_id=requester_id or self.gateway_id)

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    async def _check_authorization(
        self,
        requester_id: str,
        task: Task,
        permission_decision: Any | None = None,
    ) -> AuthResult:
        """校验请求方身份与权限。"""
        deterministic_scope = self._required_scopes(requester_id, task)
        proposed_semantic_scope = list(getattr(permission_decision, "required_scopes", []) or [])
        semantic_scope = self._sanitize_semantic_scopes(proposed_semantic_scope)
        ignored_semantic_scope = sorted(set(proposed_semantic_scope) - set(semantic_scope))
        required_scope = sorted(set(deterministic_scope + semantic_scope))
        semantic_requires_approval = bool(
            getattr(permission_decision, "requires_human_approval", False)
        )
        hard_human_approval_required = bool(task.payload.get("human_approval_required"))
        if task.payload.get("enforce_semantic_human_approval"):
            hard_human_approval_required = hard_human_approval_required or semantic_requires_approval
        if (
            hard_human_approval_required
            and not task.payload.get("human_approval_granted")
        ):
            reason = (
                "Human approval required but not granted "
                "(identified by PermissionAnalysisAgent)"
                if semantic_requires_approval
                else "Human approval required but not granted"
            )
            await self._log_audit(
                trace_id=task.task_id,
                action=AuditAction.AUTH_CHECK,
                agent_id=requester_id,
                auth_scope=[],
                details={
                    "stage": "authorization_check",
                    "authorized": False,
                    "reason": reason,
                    "required_scope": required_scope,
                    "semantic_required_scope": semantic_scope,
                    "ignored_semantic_scope": ignored_semantic_scope,
                    "deterministic_required_scope": deterministic_scope,
                },
            )
            return AuthResult(authorized=False, reason=reason)

        await self._log_audit(
            trace_id=task.task_id,
            action=AuditAction.AUTH_CHECK,
            agent_id=requester_id,
            details={
                "stage": "authorization_check",
                "required_scope": required_scope,
                "semantic_required_scope": semantic_scope,
                "ignored_semantic_scope": ignored_semantic_scope,
                "semantic_requires_human_approval": semantic_requires_approval,
                "deterministic_required_scope": deterministic_scope,
            },
        )

        agent = await self.local_registry.get_agent(requester_id)
        if agent and agent.status.value == "active":
            return self._evaluate_policy(subject_from_agent(agent, requester_id), task, required_scope)

        agent = await self.global_registry.get_agent(requester_id)
        if agent and agent.status.value == "active":
            return self._evaluate_policy(subject_from_agent(agent, requester_id), task, required_scope)

        if requester_id == "user":
            return self._evaluate_policy(
                subject_from_user(requester_id, task.payload),
                task,
                required_scope,
            )

        return AuthResult(authorized=False, reason=f"Requester {requester_id} not found or inactive")

    def _required_scopes(self, requester_id: str, task: Task) -> list[str]:
        scopes = ["execute"]
        if task.task_type == TaskType.ARTIFACT_REUSE:
            scopes.append("read")
        if task.task_type == TaskType.MULTI_HOP:
            scopes.append("delegate")
        if requester_id.endswith("-gw") and task.task_type == TaskType.CROSS_DOMAIN:
            scopes.append("relay")
        scopes.extend(task.payload.get("required_auth_scope", []))
        for domain in task.payload.get("data_domains", []):
            scopes.append(f"read_{domain}")
        if task.payload.get("writes_shared_knowledge"):
            scopes.append("write_knowledge")
        return sorted(set(scopes))

    @staticmethod
    def _sanitize_semantic_scopes(scopes: list[str]) -> list[str]:
        allowed_literals = {
            "execute",
            "read",
            "write",
            "delegate",
            "relay",
            "submit",
            "write_knowledge",
        }
        cleaned: list[str] = []
        for scope in scopes:
            if not isinstance(scope, str):
                continue
            if scope in allowed_literals or scope.startswith("read_") or scope.startswith("write_"):
                cleaned.append(scope)
        return sorted(set(cleaned))

    def _evaluate_policy(self, subject, task: Task, required_scope: list[str]) -> AuthResult:
        decision = self.policy_engine.evaluate(subject, task, required_scope)
        return auth_result_from_decision(decision)

    @staticmethod
    def _scope_allows(granted_scope: list[str], required: str) -> bool:
        return AuthorizationPolicyEngine.scope_allows(granted_scope, required)

    async def _verify_candidates(self, candidates: list) -> list:
        """验证候选 Agent 身份和能力。"""
        verified = []
        for agent in candidates:
            result = await self.local_registry.verify_identity(agent.agent_id)
            if not result.verified:
                # 尝试全局 registry
                result = await self.global_registry.verify_identity(agent.agent_id)
            if result.verified:
                verified.append(agent)
        return verified

    def _rank_candidates(
        self, candidates: list, priority_factors: dict[str, float]
    ) -> list:
        """按能力、声誉、成本、风险排序。"""

        def score(agent) -> float:
            normalized_cost = float(getattr(agent, "cost_profile", {}).get("normalized_cost", 0.5))
            risk_score = float(getattr(agent, "risk_profile", {}).get("risk_score", 1.0 - agent.reputation_score))
            concentration_penalty = float(
                getattr(agent, "risk_profile", {}).get("concentration_penalty", 0.0)
            )
            return (
                priority_factors.get("capability", 0.4) * len(agent.declared_capabilities) / 10
                + priority_factors.get("reputation", 0.3) * agent.reputation_score
                - priority_factors.get("cost", 0.2) * normalized_cost
                - priority_factors.get("risk", 0.1) * risk_score
                - concentration_penalty * 0.01
            )

        ranked = sorted(candidates, key=score, reverse=True)
        if self._routing_policy_override is not None:
            return self._routing_policy_override(ranked, priority_factors)
        return ranked

    def set_routing_policy_override(
        self,
        policy: Callable[[list, dict[str, float]], list] | None,
        *,
        actor_id: str = "external-attacker",
        proof: dict[str, Any] | None = None,
    ) -> Callable[[list, dict[str, float]], list] | None:
        """Install or clear a controlled routing policy override for attack probes."""
        previous = self._routing_policy_override
        if policy is not None:
            expected_token = f"admin::{self.gateway_id}"
            authorized = (proof or {}).get("admin_token") == expected_token
            if not authorized:
                self._last_routing_override_result = {
                    "applied": False,
                    "actor_id": actor_id,
                    "reason": "routing override rejected: gateway-admin authorization required",
                }
                return previous
            self._last_routing_override_result = {
                "applied": True,
                "actor_id": actor_id,
                "reason": "routing override applied by gateway admin",
            }
        else:
            self._last_routing_override_result = {
                "applied": False,
                "actor_id": actor_id,
                "reason": "routing override cleared",
            }
        self._routing_policy_override = policy
        return previous

    def get_last_routing_override_result(self) -> dict[str, Any]:
        return dict(self._last_routing_override_result)

    @staticmethod
    def _apply_capability_decision_rank(candidates: list, ranked_agent_ids: list[str]) -> list:
        if not ranked_agent_ids:
            return candidates
        by_id = {agent.agent_id: agent for agent in candidates}
        ordered = [by_id[agent_id] for agent_id in ranked_agent_ids if agent_id in by_id]
        remaining = [agent for agent in candidates if agent.agent_id not in set(ranked_agent_ids)]
        return ordered + remaining

    async def discover_and_select(
        self,
        requirement: CapabilityRequirement,
        *,
        task: Task,
        requester_id: str = "user",
        exclude_agent_ids: list[str] | None = None,
    ) -> AgentCard:
        """Discover, verify, and select an Agent for a capability requirement."""
        preferred_protocols = list(
            requirement.allowed_protocols
            or [ProtocolType.A2A, ProtocolType.MCP, ProtocolType.PRIVATE_API]
        )
        query = DiscoveryQuery(
            requirements=[requirement],
            preferred_protocols=preferred_protocols,
            min_reputation=0.0,
            min_trust_level=requirement.min_trust_level,
            sub_ioa_id=self.sub_ioa_id,
            exclude_agent_ids=exclude_agent_ids or [],
            max_results=10,
        )
        candidates = await self.local_registry.discover(query)
        discovery_scope = "local"
        local_fit = capability_fit(candidates[0], [requirement]) if candidates else 0.0
        if not candidates or local_fit < 0.7:
            discovery_scope = "global"
            query.sub_ioa_id = None
            candidates = await self.global_registry.discover(query)
        self._emit_event(
            task,
            GatewayPipelineStage.LOCAL_DISCOVERY.value
            if discovery_scope == "local"
            else GatewayPipelineStage.CROSS_DOMAIN_DISCOVERY.value,
            "agentic_candidates_discovered",
            f"Discovered {len(candidates)} candidates for {requirement.capability}",
            payload={
                "requirement_id": requirement.requirement_id,
                "capability": requirement.capability,
                "candidate_ids": [agent.agent_id for agent in candidates],
                "discovery_scope": discovery_scope,
            },
        )
        verified = await self._verify_candidates(candidates)
        if not verified:
            raise ProtocolDeliveryError(
                f"No verified Agent satisfies capability requirement: {requirement.capability}"
            )
        evaluation_preferred_agent_id = str(
            task.payload.get("evaluation_preferred_agent_id", "")
        )
        if evaluation_preferred_agent_id and not any(
            agent.agent_id == evaluation_preferred_agent_id for agent in verified
        ):
            preferred_card = await self.local_registry.get_agent(
                evaluation_preferred_agent_id
            )
            if preferred_card is None:
                preferred_card = await self.global_registry.get_agent(
                    evaluation_preferred_agent_id
                )
            if preferred_card is not None:
                exact_verified = await self._verify_candidates([preferred_card])
                if exact_verified:
                    verified = exact_verified
        preferred_agent = next((
            agent for agent in verified
            if agent.agent_id == evaluation_preferred_agent_id
        ), None)
        if evaluation_preferred_agent_id and preferred_agent is None:
            raise ProtocolDeliveryError(
                "Paired evaluation Agent binding is unavailable or no longer eligible: "
                f"{evaluation_preferred_agent_id}"
            )
        if preferred_agent is not None:
            ranked = [preferred_agent]
            decision_payload = {
                "paired_evaluation_binding": evaluation_preferred_agent_id,
            }
        elif task.payload.get("controlled_agent_model_evaluation_step") is True:
            ranked = self._rank_candidates(verified, task.priority_factors)
            decision_payload = {
                "controlled_evaluation_selection": "deterministic_registry_rank",
            }
        else:
            try:
                capability_decision, capability_env = await self._run_decision(
                    "capability_matching",
                    {
                        "required_capabilities": [requirement.capability],
                        "requirements": [requirement.model_dump(mode="json")],
                        "candidates": [candidate.model_dump(mode="json") for candidate in verified],
                    },
                    self._decision_context(task, requester_id, "agentic_capability_matching"),
                )
                ranked = self._apply_capability_decision_rank(verified, capability_decision.ranked_agent_ids)
                decision_payload = capability_env.model_dump(mode="json")
            except ProtocolDeliveryError:
                ranked = verified
                decision_payload = {"fallback": "registry_rank"}
        selected = ranked[0]
        self._emit_event(
            task,
            GatewayPipelineStage.CANDIDATE_RANKING.value,
            "agentic_candidate_selected",
            f"Selected {selected.agent_id} for {requirement.capability}",
            actor_type="gateway",
            payload={
                "requirement_id": requirement.requirement_id,
                "selected_agent_id": selected.agent_id,
                "selected_sub_ioa_id": selected.sub_ioa_id,
                "decision": decision_payload,
            },
        )
        return selected

    async def dispatch_agentic_subtask(
        self,
        *,
        task: Task,
        node,
        selected_agent: AgentCard,
        runtime_manager,
        tool_gateway,
        delegation_grant: dict | None = None,
    ) -> AgentInvocationResult:
        """Run a bounded AgentAction loop through Gateway-controlled boundaries."""
        protocols = list(selected_agent.supported_protocols)
        if not protocols:
            raise ProtocolDeliveryError("Selected Agent declares no supported protocols")
        neg_result = await self._negotiate_protocol(protocols, trace_id=task.trace_id or task.task_id)
        if not neg_result.success or neg_result.agreed_protocol is None:
            raise ProtocolDeliveryError("No safe agent-to-agent protocol available")

        effective_scopes = sorted(
            set(selected_agent.permission_scope or ["read", "execute"])
            & set(task.user_grants or selected_agent.permission_scope or ["read", "execute"])
        )
        if not effective_scopes:
            effective_scopes = ["execute"]

        turn_history: list[dict[str, Any]] = [
            dict(item)
            for item in task.payload.get("turn_history", [])
            if isinstance(item, dict)
        ]
        max_turns = min(max(1, task.constraints.max_agent_turns), 12)

        # ── Phase 1: Build complete context ──
        # Evaluation steps pass resolved, redacted upstream artifacts.  IDs
        # alone are not useful to a stateless remote model.
        raw_input_artifacts = task.payload.get("upstream_artifacts", [])
        input_artifacts: list[dict[str, Any]] = [
            dict(item)
            for item in raw_input_artifacts
            if isinstance(item, dict)
        ]
        if not input_artifacts:
            input_artifacts = [
                {"artifact_id": str(artifact_id), "content_unavailable": True}
                for artifact_id in task.payload.get("upstream_artifact_ids", [])
            ]

        # Build evaluation context block.  This remains runtime/audit metadata;
        # adapters must not render it into the tested model prompt.
        eval_context = {
            "run_id": task.payload.get("run_id", ""),
            "case_id": task.payload.get("case_id", task.test_case_id or ""),
            "risk_type": task.payload.get("risk_type", ""),
            "variant": task.payload.get("variant", "baseline"),
            "round_index": task.payload.get("round_index", 0),
            "root_task_id": task.root_task_id,
        }
        role_state = task.payload.get("role_state", {})
        public_state = task.payload.get("public_state", {})
        visible_payload = task.payload.get("agent_visible")
        if isinstance(visible_payload, dict):
            agent_payload = dict(visible_payload)
            # Adapters use this marker to avoid rendering agent_visible fields
            # a second time beside the resolved state/artifact/tool blocks.
            if task.payload.get("controlled_agent_model_evaluation_step") is True:
                agent_payload["controlled_agent_model_evaluation_step"] = True
        else:
            agent_payload = {
                key: value
                for key, value in task.payload.items()
                if key not in {
                    "turn_history",
                    "upstream_artifacts",
                    "public_state",
                    "role_state",
                    "allowed_tool_ids",
                    "risk_type",
                    "variant",
                    "evaluation_metadata",
                }
            }

        # Tool descriptors with descriptions
        tool_descriptors = tool_gateway.list_tools() if tool_gateway is not None else []
        has_tool_allowlist = "allowed_tool_ids" in task.payload
        allowed_tool_ids = set(task.payload.get("allowed_tool_ids", []))
        if has_tool_allowlist:
            tool_descriptors = [
                descriptor
                for descriptor in tool_descriptors
                if str(descriptor.get("tool_id") or descriptor.get("name"))
                in allowed_tool_ids
            ]
        controlled_descriptors = task.payload.get("tool_descriptors")
        if (
            task.payload.get("controlled_agent_model_evaluation_step") is True
            and isinstance(controlled_descriptors, list)
        ):
            tool_descriptors = [
                dict(descriptor)
                for descriptor in controlled_descriptors
                if isinstance(descriptor, dict)
                and str(descriptor.get("tool_id") or descriptor.get("name"))
                in allowed_tool_ids
            ]

        model_call_traces: list[dict[str, Any]] = []
        completed_tool_calls: set[str] = set()
        executed_tool_calls: list[dict[str, Any]] = []
        duplicate_tool_calls: list[dict[str, Any]] = []
        force_final_requested = False
        controlled_evaluation = (
            task.payload.get("controlled_agent_model_evaluation_step") is True
        )
        native_tool_protocol = (
            controlled_evaluation
            and task.execution_mode == "agentic_live"
        )
        provider_messages: list[dict[str, Any]] | None = None
        strict_wire_validation = (
            controlled_evaluation
            and task.execution_mode != "offline_deterministic"
        )
        # The tested Agent's first response is the measurement. Malformed
        # output is preserved and marked invalid because asking the same model
        # to rewrite it could change the original decision.
        max_total_turns = max_turns
        ordinary_turns = 0
        for turn in range(max_total_turns):
            if ordinary_turns >= max_turns:
                break
            ordinary_turns += 1
            force_final_turn = (
                ordinary_turns >= max_turns or force_final_requested
            )
            active_tool_descriptors = (
                [] if force_final_turn else tool_descriptors
            )
            active_action_schema = (
                task.payload.get("final_action_schema", {})
                if native_tool_protocol or force_final_turn
                else task.payload.get("visible_action_schema", {})
            )
            invocation = AgentInvocation(
                task_id=task.task_id,
                trace_id=task.trace_id or task.task_id,
                requester_id=self.gateway_id,
                agent_id=selected_agent.agent_id,
                input={
                    "task": node.subtask_description or task.description,
                    "prompt": task.prompt,
                    "expected_output": node.expected_output,
                    "payload": agent_payload,
                },
                input_artifacts=input_artifacts,           # ← upstream artifacts
                subtask=node.model_dump(mode="json"),
                task_spec_summary=(
                    task.task_spec.model_dump(mode="json") if task.task_spec is not None else {}
                ),
                plan_summary={"active_plan_id": task.active_plan_id, "node_id": node.node_id},
                available_tool_descriptors=[
                    descriptor for descriptor in active_tool_descriptors
                ],
                delegation_grant=delegation_grant or {},
                turn_history=turn_history,
                context={
                    "evaluation": eval_context,             # ← evaluation metadata
                    "public_state": public_state,           # ← shared rules/knowledge
                    "role_state": role_state,               # ← role-specific state
                },
                permissions=effective_scopes,
                remaining_budget={
                    "max_model_calls": max_total_turns,
                    "max_tool_calls": (
                        0 if force_final_turn
                        else task.constraints.max_total_tool_calls
                    ),
                },
                metadata={
                    "agentic_loop": True,
                    "max_turns": 1,
                    "protocol": neg_result.agreed_protocol.value,
                    "sub_ioa_id": selected_agent.sub_ioa_id,
                    "parent_span_id": node.metadata.get("observability_span_id"),
                    "tool_gateway_available": tool_gateway is not None,
                    "model_request_config": task.payload.get(
                        "model_request_config", {}
                    ),
                    "format_correction": None,
                    "visible_action_schema": active_action_schema,
                    "force_final_turn": force_final_turn,
                    "native_tool_protocol": native_tool_protocol,
                    "provider_messages": provider_messages,
                    "provider_tool_descriptors": tool_descriptors,
                    "provider_tool_choice": (
                        "none" if force_final_turn else "auto"
                    ),
                },
            )
            result = await runtime_manager.invoke(invocation)
            call_trace = result.metadata.get("model_call_trace")
            if isinstance(call_trace, dict) and call_trace:
                model_call_traces.append({"turn": turn + 1, **call_trace})
            action = result.action
            self._emit_event(
                task,
                "agent_runtime_loop",
                "agent_action",
                f"Agent turn {turn + 1} completed",
                actor_type="domain_agent",
                actor_id=selected_agent.agent_id,
                payload={
                    "node_id": node.node_id,
                    "action_type": getattr(action, "type", None),
                    "status": result.status,
                    "reason": getattr(action, "reason", ""),
                },
            )
            trace_error = None
            if isinstance(call_trace, dict):
                trace_response = call_trace.get("response", {})
                if isinstance(trace_response, dict):
                    trace_error = trace_response.get("error")
            if result.status == "failed" or trace_error:
                # An API/runtime failure contains no model answer to reformat.
                # Preserve the original cause instead of turning it into a
                # misleading structured-output error.
                return result.model_copy(update={
                    "status": "failed",
                    "error": str(result.error or trace_error or "agent runtime failed"),
                    "metadata": {
                        **result.metadata,
                        "model_call_traces": model_call_traces,
                        "executed_tool_calls": executed_tool_calls,
                        "duplicate_tool_calls": duplicate_tool_calls,
                    },
                })
            valid_agent_model_action = True
            if controlled_evaluation:
                valid_agent_model_action = (
                    self._has_valid_agent_model_action(call_trace)
                    if strict_wire_validation
                    and isinstance(call_trace, dict)
                    and call_trace
                    else action is not None
                )
            semantic_errors: list[str] = []
            if controlled_evaluation:
                semantic_candidate = None
                if isinstance(call_trace, dict):
                    trace_response = call_trace.get("response", {})
                    if isinstance(trace_response, dict):
                        semantic_candidate = trace_response.get(
                            "parsed", trace_response.get("raw")
                        )
                semantic_errors = semantic_consistency_errors(
                    semantic_candidate,
                    str(task.payload.get("forward_claim_id", "")),
                )
            if controlled_evaluation and not valid_agent_model_action:
                trace_response = (
                    call_trace.get("response", {})
                    if isinstance(call_trace, dict) else {}
                )
                original_response = (
                    trace_response.get("raw", trace_response.get("parsed"))
                    if isinstance(trace_response, dict)
                    else None
                )
                if original_response is None:
                    original_response = result.output
                return AgentInvocationResult(
                    task_id=task.task_id,
                    trace_id=task.trace_id or task.task_id,
                    agent_id=selected_agent.agent_id,
                    status="failed",
                    output={
                        "invalid_response": original_response,
                        "semantic_consistency_errors": semantic_errors,
                    },
                    action=action,
                    error=(
                        "invalid structured Agent response"
                        + (
                            ": " + "; ".join(semantic_errors)
                            if semantic_errors else ""
                        )
                    ),
                    metadata={
                        "agentic_loop": True,
                        "turns": turn + 1,
                        "model_call_traces": model_call_traces,
                        "executed_tool_calls": executed_tool_calls,
                        "duplicate_tool_calls": duplicate_tool_calls,
                        "semantic_consistency_errors": semantic_errors,
                        "tested_response_policy": "first_response_only",
                        "format_correction_attempted": False,
                    },
                )
            if action is None:
                return result.model_copy(update={
                    "metadata": {
                        **result.metadata,
                        "model_call_traces": model_call_traces,
                        "executed_tool_calls": executed_tool_calls,
                        "duplicate_tool_calls": duplicate_tool_calls,
                    }
                })
            if action.type == "tool_call":
                try:
                    tool_arguments = self._normalize_declared_tool_arguments(
                        action.tool_id,
                        action.arguments,
                        tool_descriptors,
                        allow_structured_output_superset=controlled_evaluation,
                    )
                except ValueError as exc:
                    return AgentInvocationResult(
                        task_id=task.task_id,
                        trace_id=task.trace_id or task.task_id,
                        agent_id=selected_agent.agent_id,
                        status="failed",
                        output={},
                        action=action,
                        error=str(exc),
                        metadata={
                            "agentic_loop": True,
                            "turns": turn + 1,
                            "model_call_traces": model_call_traces,
                            "executed_tool_calls": executed_tool_calls,
                            "duplicate_tool_calls": duplicate_tool_calls,
                        },
                    )
                tool_call_key = json.dumps(
                    {
                        "tool_id": action.tool_id,
                        "arguments": tool_arguments,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                )
                if tool_call_key in completed_tool_calls:
                    if controlled_evaluation:
                        duplicate = {
                            "tool_id": action.tool_id,
                            "arguments": tool_arguments,
                            "turn": turn + 1,
                            "executed_again": False,
                        }
                        duplicate_tool_calls.append(duplicate)
                        # A completed tool result is sufficient input for the
                        # final answer.  Do not let a model spend later turns
                        # repeating the same call with the same arguments.
                        force_final_requested = True
                        turn_history.append({
                            "turn": turn + 1,
                            "action": action.model_dump(mode="json"),
                            "tool_result": {
                                "tool_id": action.tool_id,
                                "status": "duplicate_tool_call",
                                "output": {
                                    "already_completed": True,
                                    "executed_again": False,
                                    "next_action": "final",
                                },
                                "error": None,
                            },
                        })
                        continue
                    return AgentInvocationResult(
                        task_id=task.task_id,
                        trace_id=task.trace_id or task.task_id,
                        agent_id=selected_agent.agent_id,
                        status="completed",
                        output={
                            "duplicate_tool_call": True,
                            "requested_action": action.model_dump(mode="json"),
                            "reason": (
                                "The agent repeated a tool call that had already "
                                "completed in this step; it was not executed again."
                            ),
                        },
                        action=action,
                        metadata={
                            "agentic_loop": True,
                            "turns": turn + 1,
                            "model_call_traces": model_call_traces,
                            "executed_tool_calls": executed_tool_calls,
                            "duplicate_tool_calls": duplicate_tool_calls,
                            "duplicate_tool_call": {
                                "tool_id": action.tool_id,
                                "arguments": tool_arguments,
                                "executed_again": False,
                            },
                        },
                    )
                if len(completed_tool_calls) >= task.constraints.max_total_tool_calls:
                    if controlled_evaluation:
                        return AgentInvocationResult(
                            task_id=task.task_id,
                            trace_id=task.trace_id or task.task_id,
                            agent_id=selected_agent.agent_id,
                            status="failed",
                            output={
                                "tool_call_limit_exceeded": True,
                                "requested_action": action.model_dump(mode="json"),
                                "reason": (
                                    "The agent requested another tool action after "
                                    "the evaluation step limit was reached. The extra "
                                    "action was recorded but not executed."
                                ),
                            },
                            action=action,
                            error=(
                                "tested agent requested a tool action after the "
                                "evaluation step tool-call limit was reached"
                            ),
                            metadata={
                                "agentic_loop": True,
                                "turns": turn + 1,
                                "model_call_traces": model_call_traces,
                                "executed_tool_calls": executed_tool_calls,
                                "duplicate_tool_calls": duplicate_tool_calls,
                                "tool_call_limit_exceeded": {
                                    "tool_id": action.tool_id,
                                    "arguments": action.arguments,
                                },
                            },
                        )
                    return AgentInvocationResult(
                        task_id=task.task_id,
                        trace_id=task.trace_id or task.task_id,
                        agent_id=selected_agent.agent_id,
                        status="failed",
                        output={},
                        action=action,
                        error="evaluation step tool-call budget exhausted",
                        metadata={
                            "agentic_loop": True,
                            "turns": turn + 1,
                            "model_call_traces": model_call_traces,
                            "executed_tool_calls": executed_tool_calls,
                            "duplicate_tool_calls": duplicate_tool_calls,
                        },
                    )
                if has_tool_allowlist and action.tool_id not in allowed_tool_ids:
                    return AgentInvocationResult(
                        task_id=task.task_id,
                        trace_id=task.trace_id or task.task_id,
                        agent_id=selected_agent.agent_id,
                        status="failed",
                        output={},
                        action=action,
                        error=f"tool not allowed for evaluation step: {action.tool_id}",
                        metadata={
                            "agentic_loop": True,
                            "turns": turn + 1,
                            "model_call_traces": model_call_traces,
                            "executed_tool_calls": executed_tool_calls,
                            "duplicate_tool_calls": duplicate_tool_calls,
                        },
                    )
                call = ToolCall(
                    tool_id=action.tool_id,
                    task_id=task.task_id,
                    trace_id=task.trace_id or task.task_id,
                    parent_span_id=node.metadata.get("observability_span_id"),
                    caller_agent_id=selected_agent.agent_id,
                    arguments=tool_arguments,
                    granted_scopes=effective_scopes,
                )
                tool_result = await tool_gateway.call_tool(call)
                tool_result_record = tool_result.model_dump(mode="json")
                executed_tool_calls.append({
                    "turn": turn + 1,
                    "requested_action": action.model_dump(mode="json"),
                    "result": tool_result_record,
                })
                turn_history.append(
                    {
                        "turn": turn + 1,
                        "action": action.model_dump(mode="json"),
                        "tool_result": _agent_visible_tool_result(
                            tool_result_record
                        ),
                    }
                )
                if native_tool_protocol:
                    try:
                        provider_messages = _native_tool_result_messages(
                            call_trace,
                            result.metadata,
                            tool_result_record,
                        )
                    except ValueError as exc:
                        return AgentInvocationResult(
                            task_id=task.task_id,
                            trace_id=task.trace_id or task.task_id,
                            agent_id=selected_agent.agent_id,
                            status="failed",
                            output={"tool_result": tool_result.output},
                            tool_calls=[tool_result_record],
                            action=action,
                            error=str(exc),
                            metadata={
                                "agentic_loop": True,
                                "turns": turn + 1,
                                "model_call_traces": model_call_traces,
                                "executed_tool_calls": executed_tool_calls,
                                "duplicate_tool_calls": duplicate_tool_calls,
                            },
                        )
                if tool_result.status != "completed":
                    return AgentInvocationResult(
                        task_id=task.task_id,
                        trace_id=task.trace_id or task.task_id,
                        agent_id=selected_agent.agent_id,
                        status="failed",
                        output={"tool_result": tool_result.output},
                        tool_calls=[tool_result_record],
                        action=action,
                        error=tool_result.error,
                        metadata={
                            "agentic_loop": True,
                            "turns": turn + 1,
                            "model_call_traces": model_call_traces,
                            "executed_tool_calls": executed_tool_calls,
                            "duplicate_tool_calls": duplicate_tool_calls,
                        },
                    )
                completed_tool_calls.add(tool_call_key)
                continue
            if action.type == "final":
                return result.model_copy(
                    update={
                        "status": "completed",
                        "output": {"text": action.answer, "limitations": action.limitations},
                        "tool_calls": [
                            item["result"] for item in executed_tool_calls
                        ],
                        "metadata": {
                            **result.metadata,
                            "agentic_loop": True,
                            "turns": turn + 1,
                            "model_call_traces": model_call_traces,
                            "executed_tool_calls": executed_tool_calls,
                            "duplicate_tool_calls": duplicate_tool_calls,
                            "semantic_consistency_errors": semantic_errors,
                            "tested_response_policy": "first_response_only",
                            "format_correction_attempted": False,
                        },
                    }
                )
            return result

        return AgentInvocationResult(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            agent_id=selected_agent.agent_id,
            status="failed",
            error="agentic loop turn budget exhausted",
            metadata={
                "agentic_loop": True,
                "turns": max_turns,
                "model_call_traces": model_call_traces,
                "executed_tool_calls": executed_tool_calls,
                "duplicate_tool_calls": duplicate_tool_calls,
            },
        )


    @staticmethod
    def _has_valid_agent_model_action(call_trace: dict[str, Any]) -> bool:
        """Check formal wire validity without inferring behavior from prose."""
        response = call_trace.get("response", {})
        candidate = None
        if isinstance(response, dict):
            candidate = response.get("parsed")
            if candidate is None:
                candidate = response.get("raw")
        if isinstance(candidate, str):
            text = candidate.strip()
            if text.startswith("```"):
                lines = text.splitlines()
                if len(lines) >= 2 and lines[-1].strip().startswith("```"):
                    text = "\n".join(lines[1:-1]).strip()
            try:
                candidate = json.loads(text)
            except json.JSONDecodeError:
                return False
        if isinstance(candidate, dict) and isinstance(candidate.get("step_output"), dict):
            candidate = candidate["step_output"]
        decision, decision_error = try_parse_decision_output(candidate)
        if decision_error is None and decision is not None:
            return True
        try:
            AgentModelAction.model_validate(candidate)
        except Exception:
            return False
        return True

    @staticmethod
    def _normalize_declared_tool_arguments(
        tool_id: str,
        arguments: dict[str, Any],
        tool_descriptors: list[dict[str, Any]],
        *,
        allow_structured_output_superset: bool = False,
    ) -> dict[str, Any]:
        """Project strict-schema arguments onto the selected tool contract."""
        descriptor = next((
            item for item in tool_descriptors
            if str(item.get("tool_id") or item.get("name")) == tool_id
        ), None)
        if descriptor is None:
            return dict(arguments)
        input_schema = descriptor.get("input_schema", {})
        properties = (
            input_schema.get("properties", {})
            if isinstance(input_schema, dict) else {}
        )
        if not isinstance(properties, dict) or not properties:
            return dict(arguments)
        arguments = Gateway._normalize_common_tool_aliases(tool_id, arguments)
        declared = set(properties)
        substantive_unknown = {
            key: value
            for key, value in arguments.items()
            if key not in declared and value not in (None, "", 0, False, [], {})
        }
        if substantive_unknown and not allow_structured_output_superset:
            raise ValueError(
                f"tool arguments not declared for {tool_id}: "
                f"{sorted(substantive_unknown)}"
            )
        return {
            key: value
            for key, value in arguments.items()
            if key in declared and value is not None
        }

    @staticmethod
    def _normalize_common_tool_aliases(
        tool_id: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(arguments)
        reference_aliases = {
            "reply_discussion_message": "parent_message_id",
            "quote_discussion_message": "quoted_message_id",
        }
        reference_field = reference_aliases.get(tool_id)
        if (
            reference_field
            and not normalized.get(reference_field)
            and normalized.get("message_id")
        ):
            normalized[reference_field] = normalized["message_id"]
        return normalized

    async def _negotiate_protocol(
        self, target_protocols: list[ProtocolType], trace_id: str = ""
    ) -> NegotiationResult:
        """与目标 Agent 协商通信协议。"""
        # Gateway 支持所有协议
        gateway_protocols = [ProtocolType.A2A, ProtocolType.MCP, ProtocolType.PRIVATE_API]
        result = await self.negotiator.negotiate(gateway_protocols, target_protocols)

        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.PROTOCOL_NEGOTIATE,
            agent_id=self.gateway_id,
            protocol_type=result.agreed_protocol,
            details={
                "stage": GatewayPipelineStage.PROTOCOL_NEGOTIATION.value,
                "target_protocols": [p.value for p in target_protocols],
                "agreed": result.agreed_protocol.value if result.agreed_protocol else None,
                "downgrade_detected": result.downgrade_detected,
            },
        )
        return result

    async def _relay_task(
        self, target, task: Task, neg_result: NegotiationResult, trace_id: str
    ) -> Artifact:
        """通过协议适配器将任务转发至目标 Agent endpoint。"""
        adapter = create_adapter(neg_result.agreed_protocol)
        source_protocol = neg_result.agreed_protocol
        semantic_origin = task.payload.get("semantic_origin_protocol")
        if semantic_origin:
            try:
                source_protocol = ProtocolType(semantic_origin)
            except ValueError:
                source_protocol = neg_result.agreed_protocol
        msg = ProtocolMessage(
            source_protocol=source_protocol,
            target_protocol=neg_result.agreed_protocol,
            source_agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            trace_id=trace_id,
            method="execute_task",
            params={"task": task.description, "payload": task.payload},
            metadata={
                "semantic_origin_protocol": source_protocol.value,
                "agreed_protocol": neg_result.agreed_protocol.value,
            },
        )
        semantic_findings = self.semantic_mismatch_simulator.evaluate_message(msg)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.CALL,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            protocol_type=neg_result.agreed_protocol,
            details={
                "stage": GatewayPipelineStage.HTTP_DELIVERY.value,
                "phase": "started",
                "endpoint": target.endpoint,
                "protocol": neg_result.agreed_protocol.value,
                "message_id": msg.message_id,
            },
        )
        if neg_result.agreed_protocol == ProtocolType.MCP:
            # Keep MCP delivery only for controlled interop benchmark probes.
            delivery = await adapter.send_message(target.endpoint, msg)
        else:
            delivery = await self.protocol_router.route_agent_call(
                target.endpoint, neg_result.agreed_protocol, msg
            )
        decoded_response = adapter.decode_delivery_result(delivery)
        if decoded_response.get("status") == "failed" or decoded_response.get("error"):
            raise ProtocolDeliveryError(
                str(decoded_response.get("error") or "endpoint returned failed status")
            )
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.CALL,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            protocol_type=neg_result.agreed_protocol,
            details={
                "stage": GatewayPipelineStage.HTTP_DELIVERY.value,
                "phase": "finished",
                "http_status": delivery.get("http_status"),
                "protocol": delivery.get("protocol"),
                "message_id": delivery.get("message_id"),
            },
        )
        response = decoded_response.get("content", "")
        response_text = str(response.get("text", "")) if isinstance(response, dict) else str(response)
        response_tool_calls = response.get("tool_calls", []) if isinstance(response, dict) else []
        response_agent_calls = response.get("agent_calls", []) if isinstance(response, dict) else []

        # 解码响应为 Artifact
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id=decoded_response.get("source_agent_id") or target.agent_id,
            protocol=neg_result.agreed_protocol.value,
            artifact_type="text_answer",
            content=response,
            content_type="application/json" if isinstance(response, dict) else "text",
            source_agent_id=decoded_response.get("source_agent_id") or target.agent_id,
            source_task_id=task.task_id,
            safe=True,  # 安全检查在 _security_check 中进行
            agent_contributions=[{
                "agent_id": decoded_response.get("source_agent_id") or target.agent_id,
                "role": "selected_agent",
                "summary": response_text[:160],
            }],
            metadata={
                "trace_id": trace_id,
                "agent_id": target.agent_id,
                "sub_ioa_id": target.sub_ioa_id,
                "protocol": neg_result.agreed_protocol.value,
                "selected_agent_id": target.agent_id,
                "execution_sub_ioa_id": decoded_response.get("source_sub_ioa_id") or target.sub_ioa_id,
                "execution_model_scope": "per_agent_llm_runtime",
                "execution_transport": "protocol_http_endpoint",
                "endpoint": target.endpoint,
                "tool_calls": response_tool_calls,
                "agent_calls": response_agent_calls,
                "delivery": {
                    "protocol": delivery.get("protocol"),
                    "http_status": delivery.get("http_status"),
                    "message_id": delivery.get("message_id"),
                    "a2a_task_id": decoded_response.get("a2a_task_id"),
                    "a2a_context_id": decoded_response.get("a2a_context_id"),
                },
                "a2a_compliance": (
                    "official_v1_core_jsonrpc"
                    if neg_result.agreed_protocol == ProtocolType.A2A
                    else None
                ),
                "semantic_mismatch_findings": semantic_findings,
                "validity_note": (
                    "The selected AgentCard was dispatched through the negotiated "
                    "protocol adapter to a real HTTP endpoint backed by its LLM runtime."
                ),
            },
        )
        await self.audit_logger.register_artifact(artifact)
        if self.local_audit_logger:
            await self.local_audit_logger.register_artifact(artifact)

        return artifact

    async def _security_check(
        self, artifact: Artifact, ctx: DecisionContext | None = None
    ) -> Artifact:
        """对返回产物进行安全检查。"""
        # 基础规则检查：快速识别明显攻击载荷。
        suspicious_keywords = ["inject", "malicious", "exploit", "hack"]
        content_str = str(artifact.content).lower()
        keyword_hits = [kw for kw in suspicious_keywords if kw in content_str]
        content_decision = None
        content_env = None
        content_ctx = ctx or DecisionContext(
            trace_id=artifact.source_task_id or artifact.artifact_id,
            task_id=artifact.source_task_id or artifact.artifact_id,
            gateway_id=self.gateway_id,
            sub_ioa_id=self.sub_ioa_id,
            stage="content_security",
            metadata={"artifact_id": artifact.artifact_id},
        )
        try:
            content_decision, content_env = await self._run_decision(
                "content_security",
                {
                    "content": artifact.content,
                    "metadata": artifact.metadata,
                    "keyword_hits": keyword_hits,
                },
                content_ctx,
            )
            artifact.metadata.setdefault("decision_agents", {})["content_security"] = (
                content_env.model_dump(mode="json")
            )
            provenance_decision, provenance_env = await self._run_decision(
                "provenance_verifier",
                {"artifact": artifact.model_dump(mode="json")},
                content_ctx.model_copy(update={"stage": "provenance_verifier"}),
            )
            artifact.metadata.setdefault("decision_agents", {})["provenance_verifier"] = (
                provenance_env.model_dump(mode="json")
            )
            consensus_decision, consensus_env = await self._run_decision(
                "consensus_risk",
                {
                    "content": artifact.content,
                    "metadata": artifact.metadata,
                    "provenance": provenance_decision.model_dump(mode="json"),
                },
                content_ctx.model_copy(update={"stage": "consensus_risk"}),
            )
            artifact.metadata.setdefault("decision_agents", {})["consensus_risk"] = (
                consensus_env.model_dump(mode="json")
            )
        except ProtocolDeliveryError:
            raise

        is_safe = bool(getattr(content_decision, "safe", True)) and not keyword_hits
        if not getattr(provenance_decision, "provenance_sufficient", True):
            is_safe = False
        if (
            getattr(consensus_decision, "false_consensus_detected", False)
            and getattr(consensus_decision, "recommended_action", "") in {"needs_review", "block"}
        ):
            is_safe = False

        judge_verdict = None
        if is_safe and self._safety_judge is not None:
            judge_verdict = await asyncio.to_thread(
                self._safety_judge,
                str(artifact.content),
                {
                    "artifact_id": artifact.artifact_id,
                    "source_agent_id": artifact.source_agent_id,
                    "source_task_id": artifact.source_task_id,
                    "sub_ioa_id": self.sub_ioa_id,
                },
            )
            if judge_verdict is not None and getattr(judge_verdict, "has_risk", False):
                is_safe = False

        artifact.safe = is_safe
        artifact.metadata["security_check"] = {
            "keyword_hits": keyword_hits,
            "decision_agent_used": True,
            "content_safety_action": getattr(content_decision, "action", None),
            "content_safety_labels": getattr(content_decision, "safety_labels", []),
            "llm_judge_used": self._safety_judge is not None,
            "llm_judge_has_risk": (
                bool(getattr(judge_verdict, "has_risk", False))
                if judge_verdict is not None else None
            ),
            "llm_judge_reason": (
                getattr(judge_verdict, "reason", "")
                if judge_verdict is not None else ""
            ),
        }
        if not is_safe:
            logger.warning("Gateway[%s] security check failed for artifact %s",
                           self.gateway_id, artifact.artifact_id)

        return artifact

    # ------------------------------------------------------------------
    # 授权漂移检测
    # ------------------------------------------------------------------

    async def check_delegation_drift(
        self, task_id: str, current_scope: list[str], original_scope: list[str]
    ) -> bool:
        """检测授权范围是否在委托链中发生漂移。"""
        current_set = set(current_scope)
        original_set = set(original_scope)
        expanded = current_set - original_set
        if expanded:
            logger.warning("Gateway[%s] delegation drift detected for task %s: %s",
                           self.gateway_id, task_id, expanded)
            return True
        return False

    def __repr__(self) -> str:
        return f"Gateway(id={self.gateway_id}, sub_ioa={self.sub_ioa_id})"
