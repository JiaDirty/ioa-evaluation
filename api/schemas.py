"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator
from typing import Any, Literal, Optional

from src.core.data_models import TaskConstraints


class ExperimentRunRequest(BaseModel):
    mode: str = "all"  # "all" | "category" | "single"
    category: Optional[str] = None
    test_id: Optional[str] = None
    topology: str = "full_mesh"
    execution_mode: Literal["agentic", "agentic_live", "scripted", "offline_deterministic"] = "agentic_live"


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


class AgenticDebugOverrides(BaseModel):
    origin_sub_ioa: str | None = None
    target_sub_ioas: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)


class TaskCreateRequest(BaseModel):
    prompt: str = ""
    description: str = ""
    user_goal: str = ""
    constraints: TaskConstraints = Field(default_factory=TaskConstraints)
    execution_mode: Literal["agentic", "agentic_live", "scripted", "offline_deterministic"] = "agentic"
    async_mode: bool = False
    debug_overrides: AgenticDebugOverrides | None = None
    # Legacy fields remain accepted for scripted compatibility only.
    origin_sub_ioa: str | None = None
    target_sub_ioas: list[str] = Field(default_factory=list)
    required_capabilities: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    compat_description_alias_used: bool = Field(default=False, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def _description_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("prompt") and data.get("description"):
            data = dict(data)
            data["prompt"] = data["description"]
            data["compat_description_alias_used"] = True
        return data

    @model_validator(mode="after")
    def _validate_prompt(self):
        if not self.prompt:
            raise ValueError("prompt is required")
        if not self.description:
            self.description = self.prompt
        return self


class TaskResponse(BaseModel):
    task_id: str
    trace_id: str
    status: str
    output: Any = None
    artifacts: list[Any] = Field(default_factory=list)
    participating_agents: list[str] = Field(default_factory=list)
    error: str | None = None


class HumanInputRequest(BaseModel):
    checkpoint_id: str
    approved: bool = False
    input: dict[str, Any] = Field(default_factory=dict)
    comment: str = ""


class TaskFeedbackRequest(BaseModel):
    rating: int | None = None
    comment: str = ""
    labels: list[str] = Field(default_factory=list)
