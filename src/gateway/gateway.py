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
import logging
from datetime import datetime
from typing import Any, Callable

from ..audit.audit_logger import AuditLogger
from ..core.data_models import (
    Artifact,
    AuditAction,
    AuthResult,
    DiscoveryQuery,
    NegotiationResult,
    ProtocolType,
    Task,
    TaskResult,
    TaskStatus,
    TaskType,
    ProtocolMessage,
)
from ..protocol.adapters import (
    ProtocolDeliveryError,
    ProtocolNegotiator,
    SemanticMismatchSimulator,
    create_adapter,
)
from ..registry.registry import Registry
from .policy import (
    AuthorizationPolicyEngine,
    auth_result_from_decision,
    subject_from_agent,
    subject_from_user,
)

logger = logging.getLogger(__name__)


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
    ) -> None:
        self.gateway_id = gateway_id
        self.sub_ioa_id = sub_ioa_id
        self.local_registry = local_registry
        self.global_registry = global_registry
        self.audit_logger = audit_logger  # 全局
        self.local_audit_logger = local_audit_logger  # 本地
        self.negotiator = ProtocolNegotiator()
        self.semantic_mismatch_simulator = SemanticMismatchSimulator()
        self.policy_engine = AuthorizationPolicyEngine()
        self._agent_runner = agent_runner
        self._safety_judge = safety_judge

        # 授权范围记录（用于检测越权漂移）
        self._auth_records: dict[str, list[str]] = {}  # task_id -> [granted_scope]

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

    # ------------------------------------------------------------------
    # 标准执行流程
    # ------------------------------------------------------------------

    async def handle_task(self, task: Task, requester_id: str = "user") -> TaskResult:
        """处理任务请求 — 标准 8 步流程。"""
        trace_id = task.task_id
        logger.info("Gateway[%s] handling task %s: %s", self.gateway_id, task.task_id, task.description[:50])

        # Step 1: Task Intake
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.CALL,
            agent_id=requester_id,
            details={"stage": "task_intake", "description": task.description},
        )

        # Step 2: Authorization Check
        auth_result = await self._check_authorization(requester_id, task)
        if not auth_result.authorized:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"Authorization denied: {auth_result.reason}",
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

        if not candidates:
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
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error="No verified candidates",
            )

        ranked = self._rank_candidates(verified, task.priority_factors)
        target = ranked[0]

        # Step 6: Protocol Negotiation
        neg_result = await self._negotiate_protocol(target.supported_protocols)
        if not neg_result.success:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"Protocol negotiation failed: {neg_result.reason}",
            )

        # Step 7: Task Relay
        try:
            artifact = await self._relay_task(target, task, neg_result, trace_id)
        except ProtocolDeliveryError as e:
            logger.warning("Gateway[%s] protocol delivery failed: %s", self.gateway_id, e)
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )

        # Step 8: Security Check & Audit
        checked = await self._security_check(artifact)
        await self._log_audit(
            trace_id=trace_id,
            action=AuditAction.SECURITY_CHECK,
            agent_id=self.gateway_id,
            target_agent_id=target.agent_id,
            output_artifact_ids=[checked.artifact_id],
            details={
                "stage": "security_check",
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
                "stage": "result_aggregation",
                "target_agent": target.agent_id,
                "protocol": neg_result.agreed_protocol.value,
                "cross_domain": cross_domain,
                "safe": checked.safe,
            },
        )

        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=checked.content,
            artifacts=[checked],
            participating_agents=[target.agent_id],
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

    async def _check_authorization(self, requester_id: str, task: Task) -> AuthResult:
        """校验请求方身份与权限。"""
        required_scope = self._required_scopes(requester_id, task)
        if (
            task.payload.get("human_approval_required")
            and not task.payload.get("human_approval_granted")
        ):
            reason = "Human approval required but not granted"
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
                },
            )
            return AuthResult(authorized=False, reason=reason)

        await self._log_audit(
            trace_id=task.task_id,
            action=AuditAction.AUTH_CHECK,
            agent_id=requester_id,
            details={"stage": "authorization_check", "required_scope": required_scope},
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
            return (
                priority_factors.get("capability", 0.4) * len(agent.declared_capabilities) / 10
                + priority_factors.get("reputation", 0.3) * agent.reputation_score
                + priority_factors.get("cost", 0.2) * 0.5  # 简化成本
                + priority_factors.get("risk", 0.1) * (1.0 - agent.reputation_score)
            )

        return sorted(candidates, key=score, reverse=True)

    async def _negotiate_protocol(
        self, target_protocols: list[ProtocolType]
    ) -> NegotiationResult:
        """与目标 Agent 协商通信协议。"""
        # Gateway 支持所有协议
        gateway_protocols = [ProtocolType.A2A, ProtocolType.MCP, ProtocolType.PRIVATE_API]
        result = await self.negotiator.negotiate(gateway_protocols, target_protocols)

        await self._log_audit(
            trace_id="",
            action=AuditAction.PROTOCOL_NEGOTIATE,
            agent_id=self.gateway_id,
            protocol_type=result.agreed_protocol,
            details={
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
        delivery = await adapter.send_message(target.endpoint, msg)
        decoded_response = adapter.decode_delivery_result(delivery)
        if decoded_response.get("status") == "failed" or decoded_response.get("error"):
            raise ProtocolDeliveryError(
                str(decoded_response.get("error") or "endpoint returned failed status")
            )
        response = decoded_response.get("content", "")

        # 解码响应为 Artifact
        artifact = Artifact(
            content=response,
            content_type="text",
            source_agent_id=decoded_response.get("source_agent_id") or target.agent_id,
            source_task_id=task.task_id,
            safe=True,  # 安全检查在 _security_check 中进行
            metadata={
                "selected_agent_id": target.agent_id,
                "execution_sub_ioa_id": decoded_response.get("source_sub_ioa_id") or target.sub_ioa_id,
                "execution_model_scope": "per_agent_llm_runtime",
                "execution_transport": "protocol_http_endpoint",
                "endpoint": target.endpoint,
                "delivery": {
                    "protocol": delivery.get("protocol"),
                    "http_status": delivery.get("http_status"),
                    "message_id": delivery.get("message_id"),
                },
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

    async def _security_check(self, artifact: Artifact) -> Artifact:
        """对返回产物进行安全检查。"""
        # 基础规则检查：快速识别明显攻击载荷。
        suspicious_keywords = ["inject", "malicious", "exploit", "hack"]
        content_str = str(artifact.content).lower()
        keyword_hits = [kw for kw in suspicious_keywords if kw in content_str]
        is_safe = not keyword_hits

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
