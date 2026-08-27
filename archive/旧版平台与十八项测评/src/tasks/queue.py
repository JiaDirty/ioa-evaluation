"""Simple asyncio task queue."""

from __future__ import annotations

import asyncio


class InMemoryTaskQueue:
    def __init__(self) -> None:
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, task_id: str) -> None:
        await self._queue.put(task_id)

    async def dequeue(self) -> str:
        return await self._queue.get()
