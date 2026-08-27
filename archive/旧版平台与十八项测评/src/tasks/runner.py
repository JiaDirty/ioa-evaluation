"""Background task runner for in-process IoA execution."""

from __future__ import annotations

import asyncio
from typing import Any

from src.core.data_models import Task, TaskStatus
from src.persistence.models import TaskRecord

from .cancellation import CancellationRegistry
from .models import RuntimeTaskStatus
from .queue import InMemoryTaskQueue


class BackgroundTaskRunner:
    def __init__(
        self,
        env: Any,
        task_store: Any,
        event_bus: Any,
        cancellation_registry: CancellationRegistry,
        artifact_store: Any | None = None,
        queue: InMemoryTaskQueue | None = None,
    ) -> None:
        self.env = env
        self.task_store = task_store
        self.event_bus = event_bus
        self.cancellation_registry = cancellation_registry
        self.artifact_store = artifact_store
        self.queue = queue or InMemoryTaskQueue()
        self._tasks: dict[str, Task] = {}
        self._worker: asyncio.Task | None = None
        self._stopping = False

    async def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._stopping = False
            self._worker = asyncio.create_task(self._work_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
            self._worker = None

    async def submit(self, task: Task) -> None:
        self._tasks[task.task_id] = task
        existing = self.task_store.get_task(task.task_id)
        if existing is None:
            self.task_store.create_task(
                TaskRecord(
                    task_id=task.task_id,
                    trace_id=task.trace_id or task.task_id,
                    status=RuntimeTaskStatus.QUEUED.value,
                    description=task.description,
                    payload={"task": task.model_dump(mode="json")},
                )
            )
        else:
            self.task_store.update_task_status(task.task_id, RuntimeTaskStatus.QUEUED.value)
        await self.queue.enqueue(task.task_id)
        self._emit(task, "task_queued", RuntimeTaskStatus.QUEUED.value, "Task queued")

    async def run_once(self, task_id: str | None = None) -> None:
        if task_id is None:
            task_id = await self.queue.dequeue()
        task = self._tasks.get(task_id)
        if task is None:
            record = self.task_store.get_task(task_id)
            if record is None:
                return
            task_payload = record.payload.get("task", {})
            task = Task(**task_payload)
            self._tasks[task_id] = task

        if self.cancellation_registry.is_cancel_requested(task_id):
            self.task_store.update_task_status(task_id, RuntimeTaskStatus.CANCELLED.value, error="cancelled before run")
            self._emit(task, "task_cancelled", RuntimeTaskStatus.CANCELLED.value, "Task cancelled before run")
            self.cancellation_registry.clear(task_id)
            return

        self.task_store.update_task_status(task_id, RuntimeTaskStatus.RUNNING.value)
        self._emit(task, "task_running", RuntimeTaskStatus.RUNNING.value, "Task runner started")
        try:
            result = await self.env.submit_task(task)
            response = {
                "task_id": result.task_id,
                "trace_id": task.trace_id or task.task_id,
                "status": result.status.value if hasattr(result.status, "value") else str(result.status),
                "output": result.output,
                "artifacts": [artifact.model_dump(mode="json") for artifact in result.artifacts],
                "participating_agents": result.participating_agents,
                "error": result.error,
            }
            if self.artifact_store is not None:
                for artifact in result.artifacts:
                    self.artifact_store.append(artifact, trace_id=response["trace_id"])
            if self.cancellation_registry.is_cancel_requested(task_id):
                self.task_store.update_task_status(
                    task_id,
                    RuntimeTaskStatus.CANCELLED.value,
                    result=response,
                    error="cancelled by user",
                )
                self._emit(task, "task_cancelled", RuntimeTaskStatus.CANCELLED.value, "Task cancelled by user")
            else:
                status = (
                    RuntimeTaskStatus.COMPLETED.value
                    if result.status == TaskStatus.COMPLETED
                    else RuntimeTaskStatus.FAILED.value
                )
                self.task_store.update_task_status(task_id, status, result=response, error=result.error)
                self._emit(task, "task_completed" if status == "completed" else "task_failed", status, "Task runner finished")
        except Exception as exc:
            self.task_store.update_task_status(task_id, RuntimeTaskStatus.FAILED.value, error=str(exc))
            self._emit(task, "task_failed", RuntimeTaskStatus.FAILED.value, str(exc))
        finally:
            self.cancellation_registry.clear(task_id)

    async def _work_loop(self) -> None:
        while not self._stopping:
            task_id = await self.queue.dequeue()
            await self.run_once(task_id)

    def _emit(self, task: Task, event_type: str, status: str, message: str) -> None:
        self.event_bus.emit(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            stage="task_runner",
            event_type=event_type,
            actor_type="task_runner",
            actor_id="background",
            message=message,
            status=status,
        )
