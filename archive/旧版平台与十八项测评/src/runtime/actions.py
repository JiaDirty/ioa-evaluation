"""Structured actions emitted by Agent runtimes in agentic mode."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from ..core.data_models import CapabilityRequirement


class ToolAction(BaseModel):
    type: Literal["tool_call"] = "tool_call"
    tool_id: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""


class DelegationAction(BaseModel):
    type: Literal["delegate"] = "delegate"
    objective: str
    required_capabilities: list[CapabilityRequirement] = Field(default_factory=list)
    requested_scopes: list[str] = Field(default_factory=list)
    input_artifact_ids: list[str] = Field(default_factory=list)
    expected_output: str = ""
    reason: str = ""


class AskUserAction(BaseModel):
    type: Literal["ask_user"] = "ask_user"
    question: str
    required_fields: list[str] = Field(default_factory=list)
    reason: str = ""


class ReplanAction(BaseModel):
    type: Literal["replan"] = "replan"
    reason: str
    new_facts: list[str] = Field(default_factory=list)
    blocked_requirements: list[str] = Field(default_factory=list)


class FinalAction(BaseModel):
    type: Literal["final"] = "final"
    answer: Any
    artifact_type: str = "text_answer"
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)


class FailAction(BaseModel):
    type: Literal["fail"] = "fail"
    error_code: str
    message: str
    recoverable: bool = False


AgentAction = Annotated[
    ToolAction | DelegationAction | AskUserAction | ReplanAction | FinalAction | FailAction,
    Field(discriminator="type"),
]
