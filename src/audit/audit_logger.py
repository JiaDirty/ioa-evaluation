"""Audit Logger — 审计日志器。

记录完整调用链，支持跨域追溯、产物来源追踪、责任归因。
基于 TrinityGuard 的结构化日志系统扩展。
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from ..core.data_models import (
    AuditAction,
    AuditEntry,
    AuditMetrics,
    Artifact,
)

logger = logging.getLogger(__name__)


class AuditLogger:
    """全局审计日志器。

    记录跨域完整调用链，支持：
    - 调用链查询
    - 产物来源追溯
    - 错误归因定位
    - 审计指标计算
    """

    def __init__(self, logger_id: str = "global") -> None:
        self.logger_id = logger_id
        self._entries: list[AuditEntry] = []
        self._artifacts: dict[str, Artifact] = {}
        self._step_counters: dict[str, int] = {}  # trace_id -> next step_index

    # ------------------------------------------------------------------
    # 日志记录
    # ------------------------------------------------------------------

    async def log(self, entry: AuditEntry) -> str:
        """记录一条审计日志。自动分配 step_index。"""
        if entry.trace_id not in self._step_counters:
            self._step_counters[entry.trace_id] = 0
        entry.step_index = self._step_counters[entry.trace_id]
        self._step_counters[entry.trace_id] += 1
        self._entries.append(entry)
        logger.debug("Audit[%s] step %d: %s -> %s (%s)",
                      self.logger_id, entry.step_index, entry.agent_id,
                      entry.target_agent_id or "N/A", entry.action.value)
        return entry.entry_id

    async def log_action(
        self,
        trace_id: str,
        action: AuditAction,
        agent_id: str,
        sub_ioa_id: str,
        gateway_id: str | None = None,
        target_agent_id: str | None = None,
        auth_scope: list[str] | None = None,
        protocol_type: Any = None,
        input_artifact_ids: list[str] | None = None,
        output_artifact_ids: list[str] | None = None,
        parent_trace_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """便捷方法：记录一条审计日志。"""
        entry = AuditEntry(
            trace_id=trace_id,
            step_index=0,  # 会被自动覆盖
            action=action,
            agent_id=agent_id,
            sub_ioa_id=sub_ioa_id,
            gateway_id=gateway_id,
            auth_scope=auth_scope or [],
            protocol_type=protocol_type,
            input_artifact_ids=input_artifact_ids or [],
            output_artifact_ids=output_artifact_ids or [],
            parent_trace_id=parent_trace_id,
            target_agent_id=target_agent_id,
            details=details or {},
        )
        return await self.log(entry)

    # ------------------------------------------------------------------
    # 产物管理
    # ------------------------------------------------------------------

    async def register_artifact(self, artifact: Artifact) -> str:
        """注册产物，用于来源追踪。"""
        self._artifacts[artifact.artifact_id] = artifact
        return artifact.artifact_id

    async def get_artifact(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    async def query_chain(self, trace_id: str) -> list[AuditEntry]:
        """查询完整调用链。"""
        return [e for e in self._entries if e.trace_id == trace_id]

    async def query_by_agent(self, agent_id: str) -> list[AuditEntry]:
        """查询某 Agent 的所有审计记录。"""
        return [e for e in self._entries if e.agent_id == agent_id]

    async def query_by_sub_ioa(self, sub_ioa_id: str) -> list[AuditEntry]:
        """查询某子生态的所有审计记录。"""
        return [e for e in self._entries if e.sub_ioa_id == sub_ioa_id]

    async def trace_origin(self, artifact_id: str) -> list[AuditEntry]:
        """追溯产物来源。

        从产出该产物的审计记录开始，沿调用链回溯。
        """
        # 找到产出该产物的记录
        producing_entries = [
            e for e in self._entries if artifact_id in e.output_artifact_ids
        ]
        if not producing_entries:
            return []

        origin_entry = producing_entries[0]
        trace_id = origin_entry.trace_id

        # 返回该调用链的完整记录
        return await self.query_chain(trace_id)

    # ------------------------------------------------------------------
    # 错误归因
    # ------------------------------------------------------------------

    async def find_error_source(
        self, trace_id: str, error_description: str = ""
    ) -> dict[str, Any]:
        """定位错误引入点。

        分析调用链，识别最可能引入错误的环节。
        返回归因结果。
        """
        chain = await self.query_chain(trace_id)
        if not chain:
            return {"found": False, "reason": "No chain found"}

        # 归因逻辑：分析每一步的输入输出和产物来源闭合情况。
        attribution = {
            "found": True,
            "trace_id": trace_id,
            "chain_length": len(chain),
            "steps": [],
            "suspect_step": None,
            "confidence": 0.0,
        }

        for entry in chain:
            step_info = {
                "step": entry.step_index,
                "agent": entry.agent_id,
                "sub_ioa": entry.sub_ioa_id,
                "action": entry.action.value,
                "target": entry.target_agent_id,
                "input_count": len(entry.input_artifact_ids),
                "output_count": len(entry.output_artifact_ids),
            }
            attribution["steps"].append(step_info)

        # 如果只有一个步骤，直接归因
        if len(chain) == 1:
            attribution["suspect_step"] = chain[0].step_index
            attribution["confidence"] = 0.9
        # 否则标记中间步骤为嫌疑最大
        elif len(chain) > 2:
            mid = len(chain) // 2
            attribution["suspect_step"] = chain[mid].step_index
            attribution["confidence"] = 0.6

        return attribution

    # ------------------------------------------------------------------
    # 指标计算
    # ------------------------------------------------------------------

    async def compute_metrics(self) -> AuditMetrics:
        """计算审计指标。"""
        total_entries = len(self._entries)
        total_traces = len(self._step_counters)

        # 调用链完整率：检查每条链是否有断裂
        complete_chains = 0
        for trace_id, expected_steps in self._step_counters.items():
            chain = [e for e in self._entries if e.trace_id == trace_id]
            if len(chain) == expected_steps:
                complete_chains += 1
        chain_completeness = complete_chains / total_traces if total_traces > 0 else 1.0

        # 来源覆盖率：有 source 信息的记录比例
        with_source = sum(1 for e in self._entries if e.target_agent_id)
        source_coverage = with_source / total_entries if total_entries > 0 else 1.0

        # 归因准确率：输出产物必须存在，且产物来源能与审计记录闭合。
        producing_entries = [e for e in self._entries if e.output_artifact_ids]
        traceable_outputs = 0
        total_outputs = 0
        for entry in producing_entries:
            for artifact_id in entry.output_artifact_ids:
                total_outputs += 1
                artifact = self._artifacts.get(artifact_id)
                if not artifact:
                    continue
                source_matches = (
                    not entry.target_agent_id
                    or artifact.source_agent_id == entry.target_agent_id
                )
                task_matches = (
                    not artifact.source_task_id
                    or artifact.source_task_id == entry.trace_id
                )
                if artifact.source_agent_id and source_matches and task_matches:
                    traceable_outputs += 1
        attribution_accuracy = (
            traceable_outputs / total_outputs
            if total_outputs > 0 else chain_completeness
        )

        return AuditMetrics(
            chain_completeness=chain_completeness,
            attribution_accuracy=attribution_accuracy,
            source_coverage=source_coverage,
            total_entries=total_entries,
            total_traces=total_traces,
        )

    # ------------------------------------------------------------------
    # 清理
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """清空所有审计数据。"""
        self._entries.clear()
        self._artifacts.clear()
        self._step_counters.clear()

    @property
    def entry_count(self) -> int:
        return len(self._entries)

    def __repr__(self) -> str:
        return f"AuditLogger(id={self.logger_id}, entries={len(self._entries)}, artifacts={len(self._artifacts)})"
