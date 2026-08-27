"""Runtime task status models."""

from __future__ import annotations

from enum import Enum


class RuntimeTaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_HUMAN_APPROVAL = "waiting_human_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    RETRYING = "retrying"
