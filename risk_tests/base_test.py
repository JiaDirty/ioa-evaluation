"""IoA 风险测试基类 — 支持真实 LLM 调用。"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

from src.core.data_models import RiskLevel, TaskResult, TaskStatus, TestResult
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

    def require_task_completed(self, result: TaskResult, evidence_name: str) -> None:
        """Require successful task execution before using it as measurement evidence."""
        if result.status != TaskStatus.COMPLETED:
            raise EvaluationInvalidError(
                f"{evidence_name} is invalid because task {result.task_id} "
                f"did not complete: {result.error or result.status.value}"
            )
