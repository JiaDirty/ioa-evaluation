"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel
from typing import Optional


class ExperimentRunRequest(BaseModel):
    mode: str = "all"  # "all" | "category" | "single"
    category: Optional[str] = None
    test_id: Optional[str] = None
    topology: str = "full_mesh"


class ExperimentRunResponse(BaseModel):
    experiment_id: str
    status: str


class ReportSummary(BaseModel):
    experiment_id: str
    timestamp: str
    total_tests: int
    passed: int
    failed: int


class TopologyUpdate(BaseModel):
    style: str  # "full_mesh" | "star" | "chain"
