"""Task Marketplace — 任务市场。

任务发布、动态发现、组队、编排和跨域委托的中心机制。
驱动 IoA 生态的任务流动。
"""

from __future__ import annotations

import logging
from typing import Any

from ..core.data_models import (
    Artifact,
    Task,
    TaskResult,
    TaskStatus,
    TaskType,
)
from ..gateway.gateway import Gateway

logger = logging.getLogger(__name__)


class TaskMarketplace:
    """任务市场。

    管理任务的生命周期：发布 → 发现 → 组队 → 编排 → 执行 → 聚合。
    """

    def __init__(self, marketplace_id: str = "global") -> None:
        self.marketplace_id = marketplace_id
        self._tasks: dict[str, Task] = {}
        self._results: dict[str, TaskResult] = {}
        self._gateways: dict[str, Gateway] = {}  # sub_ioa_id -> Gateway
        self._topology = None

    # ------------------------------------------------------------------
    # Gateway 注册
    # ------------------------------------------------------------------

    def register_gateway(self, sub_ioa_id: str, gateway: Gateway) -> None:
        """注册 Sub-IoA 的 Gateway。"""
        self._gateways[sub_ioa_id] = gateway
        logger.info("Marketplace registered gateway for %s", sub_ioa_id)

    def set_topology(self, topology) -> None:
        """Attach topology controller used to validate cross-domain reachability."""
        self._topology = topology

    # ------------------------------------------------------------------
    # 任务管理
    # ------------------------------------------------------------------

    async def publish_task(self, task: Task) -> str:
        """发布任务到市场。"""
        self._tasks[task.task_id] = task
        logger.info("Marketplace published task %s: %s (type=%s)",
                     task.task_id, task.description[:50], task.task_type.value)
        return task.task_id

    async def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    async def list_tasks(self, status: TaskStatus | None = None) -> list[Task]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        return tasks

    # ------------------------------------------------------------------
    # 任务执行
    # ------------------------------------------------------------------

    async def execute_task(self, task: Task) -> TaskResult:
        """根据任务类型执行任务。

        - single_domain: 直接分发到对应 Sub-IoA
        - cross_domain: 通过 Gateway 中继到多个 Sub-IoA
        - multi_hop: 多跳委托，逐级转发
        - artifact_reuse: 产物复用，前序任务输出作为后序输入
        """
        self._tasks[task.task_id] = task
        task.status = TaskStatus.IN_PROGRESS

        try:
            if task.task_type == TaskType.SINGLE_DOMAIN:
                result = await self._execute_single_domain(task)
            elif task.task_type == TaskType.CROSS_DOMAIN:
                result = await self._execute_cross_domain(task)
            elif task.task_type == TaskType.MULTI_HOP:
                result = await self._execute_multi_hop(task)
            elif task.task_type == TaskType.ARTIFACT_REUSE:
                result = await self._execute_artifact_reuse(task)
            else:
                result = TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error=f"Unknown task type: {task.task_type}",
                )
        except Exception as e:
            logger.exception("Marketplace failed to execute task %s", task.task_id)
            result = TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=str(e),
            )

        task.status = result.status
        self._results[task.task_id] = result
        return result

    async def get_result(self, task_id: str) -> TaskResult | None:
        return self._results.get(task_id)

    def list_results(self) -> list[TaskResult]:
        """Return all marketplace task results recorded during the run."""
        return list(self._results.values())

    # ------------------------------------------------------------------
    # 单域任务
    # ------------------------------------------------------------------

    async def _execute_single_domain(self, task: Task) -> TaskResult:
        """单域任务：直接分发到对应 Sub-IoA。"""
        target_sub_ioa = task.payload.get("target_sub_ioa", "")
        gateway = self._gateways.get(target_sub_ioa)
        if not gateway:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"No gateway for sub-ioa: {target_sub_ioa}",
            )
        return await gateway.handle_task(task)

    # ------------------------------------------------------------------
    # 跨域任务
    # ------------------------------------------------------------------

    async def _execute_cross_domain(self, task: Task) -> TaskResult:
        """跨域任务：通过多个 Gateway 协同完成。"""
        target_sub_ioas = task.payload.get("target_sub_ioas", [])
        if not target_sub_ioas:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error="No target sub-ioas specified for cross-domain task",
            )

        all_artifacts: list[Artifact] = []
        all_agents: list[str] = []
        errors: list[str] = []

        # 通过第一个 Gateway 入口处理
        primary_gateway = self._gateways.get(target_sub_ioas[0])
        if not primary_gateway:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"No gateway for primary sub-ioa: {target_sub_ioas[0]}",
            )

        # 主 Gateway 处理
        primary_result = await primary_gateway.handle_task(
            self._task_for_sub_ioa(task, target_sub_ioas[0])
        )
        if primary_result.status == TaskStatus.COMPLETED:
            all_artifacts.extend(primary_result.artifacts)
            all_agents.extend(primary_result.participating_agents)
        else:
            errors.append(primary_result.error or f"Primary execution in {target_sub_ioas[0]} failed")

        # 中继到其他 Sub-IoA
        for sub_ioa_id in target_sub_ioas[1:]:
            if not self._is_reachable(target_sub_ioas[0], sub_ioa_id):
                errors.append(f"Sub-IoA {sub_ioa_id} not reachable from {target_sub_ioas[0]}")
                continue
            relay_gateway = self._gateways.get(sub_ioa_id)
            if not relay_gateway:
                errors.append(f"No gateway for sub-ioa: {sub_ioa_id}")
                continue

            relay_result = await primary_gateway.relay_to_sub_ioa(
                self._task_for_sub_ioa(task, sub_ioa_id),
                relay_gateway,
            )
            if relay_result.status == TaskStatus.COMPLETED:
                all_artifacts.extend(relay_result.artifacts)
                all_agents.extend(relay_result.participating_agents)
            else:
                errors.append(relay_result.error or f"Relay to {sub_ioa_id} failed")

        status = TaskStatus.COMPLETED if not errors else TaskStatus.FAILED
        return TaskResult(
            task_id=task.task_id,
            status=status,
            output=[a.content for a in all_artifacts],
            artifacts=all_artifacts,
            error="; ".join(errors) if errors else None,
            participating_agents=list(set(all_agents)),
        )

    def _is_reachable(self, source_sub_ioa: str, target_sub_ioa: str) -> bool:
        if source_sub_ioa == target_sub_ioa:
            return True
        if self._topology is None:
            return True
        return self._topology.is_connected(source_sub_ioa, target_sub_ioa)

    @staticmethod
    def _task_for_sub_ioa(task: Task, sub_ioa_id: str) -> Task:
        cap_map = task.payload.get("required_capabilities_by_sub_ioa", {})
        if sub_ioa_id not in cap_map:
            return task
        routed = task.model_copy(deep=True)
        routed.required_capabilities = list(cap_map.get(sub_ioa_id, []))
        routed.payload["target_sub_ioa"] = sub_ioa_id
        return routed

    # ------------------------------------------------------------------
    # 多跳委托
    # ------------------------------------------------------------------

    async def _execute_multi_hop(self, task: Task) -> TaskResult:
        """多跳委托：逐级转发，测试授权漂移。"""
        hop_chain = task.payload.get("hop_chain", [])
        if not hop_chain:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error="No hop_chain specified for multi-hop task",
            )

        current_task = task
        all_artifacts: list[Artifact] = []
        all_agents: list[str] = []
        current_scope = task.payload.get("initial_scope", ["read"])

        for i, hop in enumerate(hop_chain):
            sub_ioa_id = hop.get("sub_ioa_id", "")
            gateway = self._gateways.get(sub_ioa_id)
            if not gateway:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error=f"No gateway for hop {i}: {sub_ioa_id}",
                )

            # 检测授权漂移
            granted_scope = hop.get("granted_scope", current_scope)
            if set(granted_scope) - set(current_scope):
                logger.warning("Marketplace: authorization drift at hop %d: %s -> %s",
                               i, current_scope, granted_scope)

            result = await gateway.handle_task(current_task, requester_id=f"hop-{i}")
            if result.status != TaskStatus.COMPLETED:
                return result

            all_artifacts.extend(result.artifacts)
            all_agents.extend(result.participating_agents)
            current_scope = granted_scope  # 更新当前授权范围

        return TaskResult(
            task_id=task.task_id,
            status=TaskStatus.COMPLETED,
            output=[a.content for a in all_artifacts],
            artifacts=all_artifacts,
            participating_agents=list(set(all_agents)),
        )

    # ------------------------------------------------------------------
    # 产物复用
    # ------------------------------------------------------------------

    async def _execute_artifact_reuse(self, task: Task) -> TaskResult:
        """产物复用任务：前序任务输出作为后序任务输入。"""
        source_task_id = task.payload.get("source_task_id", "")
        source_result = self._results.get(source_task_id)

        if not source_result or not source_result.artifacts:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"Source task {source_task_id} not found or has no artifacts",
            )

        # 将前序产物注入当前任务
        task.payload["input_artifacts"] = [a.artifact_id for a in source_result.artifacts]

        target_sub_ioa = task.payload.get("target_sub_ioa", "")
        gateway = self._gateways.get(target_sub_ioa)
        if not gateway:
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.FAILED,
                error=f"No gateway for sub-ioa: {target_sub_ioa}",
            )

        return await gateway.handle_task(task)
