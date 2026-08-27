"""IoA 风险测试基类 — 支持真实 LLM 调用。"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from src.core.data_models import RiskLevel, Task, TaskResult, TaskStatus, TaskType, TestResult
from src.experiment.exceptions import EvaluationInvalidError
from .realism import get_realism_profile

logger = logging.getLogger(__name__)


class BaseIoARiskTest(ABC):
    """IoA 风险测试抽象基类。

    子类实现 run() 方法，使用 env 中的真实 AG2 Agent、
    AttackInjector（PAIR风格）和 LLMJudge 进行测试。
    """

    test_id: str = ""
    test_name: str = ""
    category: str = ""
    description: str = ""

    @abstractmethod
    async def run(self, env: Any, **kwargs) -> TestResult:
        """执行测试，返回结果。"""

    def make_result(
        self,
        passed: bool,
        risk_level: RiskLevel = RiskLevel.LOW,
        confidence: float = 0.0,
        explanation: str = "",
        metrics: dict[str, float] | None = None,
        details: dict[str, Any] | None = None,
        realism: dict[str, Any] | None = None,
    ) -> TestResult:
        return TestResult(
            test_id=self.test_id,
            test_name=self.test_name,
            category=self.category,
            passed=passed,
            risk_level=risk_level,
            confidence=confidence,
            explanation=explanation,
            metrics=metrics or {},
            details=details or {},
            realism=realism or get_realism_profile(self.test_id),
        )

    async def run_agent_task(self, env: Any, sub_ioa_id: str, task: str) -> str:
        """在 AG2 Agent 上执行任务（在线程中运行同步 AG2 调用）。"""
        try:
            return await asyncio.to_thread(env.run_agent_task, sub_ioa_id, task)
        except Exception as e:
            logger.warning("Agent task failed for %s: %s", sub_ioa_id, e)
            raise EvaluationInvalidError(
                f"Agent backend failed for Sub-IoA '{sub_ioa_id}': {e}"
            ) from e

    async def run_gateway_prompt(
        self,
        env: Any,
        sub_ioa_id: str,
        prompt: str,
        *,
        required_capabilities: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        task_type: TaskType = TaskType.SINGLE_DOMAIN,
        evidence_name: str | None = None,
    ) -> TaskResult:
        """Run an attack prompt through Marketplace/Gateway/HTTP endpoint."""
        default_capabilities = {
            "finance": ["financial_analysis"],
            "healthcare": ["clinical_analysis"],
            "travel": ["travel_planning"],
            "news": ["news_aggregation"],
        }
        task_payload = dict(payload or {})
        if task_type == TaskType.CROSS_DOMAIN:
            task_payload.setdefault("target_sub_ioas", [sub_ioa_id])
        else:
            task_payload.setdefault("target_sub_ioa", sub_ioa_id)
        task = Task(
            task_type=task_type,
            description=prompt,
            required_capabilities=required_capabilities or default_capabilities.get(sub_ioa_id, []),
            payload=task_payload,
        )
        result = await env.submit_task(task)
        self.require_task_completed(result, evidence_name or f"{self.test_id} gateway prompt")
        return result

    async def judge_attack(self, env: Any, risk_type: str, goal: str, response: str) -> tuple[bool, str]:
        """用 LLM Judge 评估攻击是否成功。"""
        try:
            judge = env.get_judge(risk_type)
            succeeded, reason = await asyncio.to_thread(judge.judge_attack_success, goal, response)
            if reason == "Judge evaluation failed":
                raise EvaluationInvalidError(f"LLM judge returned no valid verdict for {risk_type}")
            return succeeded, reason
        except Exception as e:
            logger.warning("Judge failed: %s", e)
            if isinstance(e, EvaluationInvalidError):
                raise
            raise EvaluationInvalidError(f"LLM judge failed for {risk_type}: {e}") from e

    def record_test_decision(self, details: dict[str, Any], envelope: Any) -> None:
        """Record test-level Decision Agent evidence for report coverage."""
        agent_name = getattr(envelope, "agent_name", None)
        if not agent_name:
            raise EvaluationInvalidError("Decision envelope is missing agent_name")
        details.setdefault("decision_agents", {})[str(agent_name)] = envelope.model_dump(mode="json")

    def record_required_test_decisions(
        self,
        env: Any,
        details: dict[str, Any],
        evidence: dict[str, Any],
    ) -> None:
        """Run test-specific semantic Decision Agents over concrete test evidence."""
        from src.decision_agents import (
            AgencyErosionAgent,
            AuditAttributionAgent,
            ConsensusRiskAgent,
            DecisionAgentError,
            DecisionContext,
            DelegationDriftAgent,
            DeterministicDecisionClient,
            DiscussionIntegrityAgent,
            HumanAgencyAgent,
            IncentiveAlignmentAgent,
            InteropSemanticMapperAgent,
            NormDriftAgent,
            ProtocolSemanticsAgent,
            RegistryRiskAgent,
            ReputationFairnessAgent,
            RoutingManipulationAgent,
            RumorAssessmentAgent,
        )

        agent_classes = {
            "AgencyErosionAgent": AgencyErosionAgent,
            "AuditAttributionAgent": AuditAttributionAgent,
            "ConsensusRiskAgent": ConsensusRiskAgent,
            "DelegationDriftAgent": DelegationDriftAgent,
            "DiscussionIntegrityAgent": DiscussionIntegrityAgent,
            "HumanAgencyAgent": HumanAgencyAgent,
            "IncentiveAlignmentAgent": IncentiveAlignmentAgent,
            "InteropSemanticMapperAgent": InteropSemanticMapperAgent,
            "NormDriftAgent": NormDriftAgent,
            "ProtocolSemanticsAgent": ProtocolSemanticsAgent,
            "RegistryRiskAgent": RegistryRiskAgent,
            "ReputationFairnessAgent": ReputationFairnessAgent,
            "RoutingManipulationAgent": RoutingManipulationAgent,
            "RumorAssessmentAgent": RumorAssessmentAgent,
        }
        profile = get_realism_profile(self.test_id)
        client = getattr(env, "_decision_client", None) or DeterministicDecisionClient()
        for agent_name in profile.get("test_required_decision_agents", []):
            agent_cls = agent_classes.get(agent_name)
            if agent_cls is None:
                raise EvaluationInvalidError(
                    f"{self.test_id} has unsupported required Decision Agent: {agent_name}"
                )
            agent = agent_cls(client)
            stage = agent_name.removesuffix("Agent")
            stage = "".join(
                f"_{ch.lower()}" if ch.isupper() else ch
                for ch in stage
            ).lstrip("_")
            ctx = DecisionContext(
                trace_id=f"{self.test_id}:{stage}",
                task_id=self.test_id,
                stage=stage,
                requester_id="risk_test",
                metadata={"test_id": self.test_id, "test_name": self.test_name},
            )
            try:
                decision = agent.decide(
                    {"test_id": self.test_id, "evidence": evidence},
                    ctx,
                )
            except DecisionAgentError as e:
                raise EvaluationInvalidError(
                    f"{self.test_id} Decision Agent {agent_name} failed: {e}"
                ) from e
            self.record_test_decision(details, agent.envelope(decision, ctx))

    def require_task_completed(self, result: TaskResult, evidence_name: str) -> None:
        """Require successful task execution before using it as measurement evidence."""
        if result.status != TaskStatus.COMPLETED:
            raise EvaluationInvalidError(
                f"{evidence_name} is invalid because task {result.task_id} "
                f"did not complete: {result.error or result.status.value}"
            )
        profile = get_realism_profile(self.test_id)
        required_agents = profile.get(
            "gateway_required_decision_agents",
            profile.get("required_decision_agents", []),
        )
        if required_agents:
            self.require_decision_evidence(result, required_agents, evidence_name)

    def require_decision_evidence(
        self, task_result: TaskResult, required_agents: list[str], context: str
    ) -> None:
        """Require structured Decision Agent evidence on gateway-produced artifacts."""
        observed: set[str] = set()
        for artifact in task_result.artifacts or []:
            decisions = (artifact.metadata or {}).get("decision_agents", {})
            if not isinstance(decisions, dict):
                continue
            for envelope in decisions.values():
                if not isinstance(envelope, dict):
                    continue
                agent_name = envelope.get("agent_name") or envelope.get("agent")
                if agent_name:
                    observed.add(str(agent_name))

        missing = [agent for agent in required_agents if agent not in observed]
        if missing:
            raise EvaluationInvalidError(
                f"{context} missing decision agent evidence: {missing}"
            )
