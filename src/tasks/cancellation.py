"""In-process cancellation flags for background tasks."""

from __future__ import annotations


class CancellationRegistry:
    def __init__(self) -> None:
        self._cancel_requested: set[str] = set()

    def request_cancel(self, task_id: str) -> None:
        self._cancel_requested.add(task_id)

    def is_cancel_requested(self, task_id: str) -> bool:
        return task_id in self._cancel_requested

    def clear(self, task_id: str) -> None:
        self._cancel_requested.discard(task_id)
