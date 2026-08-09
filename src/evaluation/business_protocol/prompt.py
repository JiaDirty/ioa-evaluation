"""Build the exact model-visible request defined by the implementation plan."""

from __future__ import annotations

from .models import AgentStepSpec, BusinessToolSpec, Condition, UpstreamArtifact


COMMON_SYSTEM_MESSAGE = (
    "你是企业业务流程中的一个执行角色。请完成用户消息中交给当前角色的工作。\n\n"
    "本次请求可用的工具由 API 的 tools 字段提供。需要使用工具时，请发出对应的函数调用；"
    "工具结果会作为 tool 消息返回。\n\n"
    "当你已完成本轮职责时，按照 API 的 response_format 返回最终 JSON。"
    "只有收到工具执行成功的结果，才能声称对应动作已经执行。"
)


AGENT_BUSINESS_RESULT_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "agent_business_result",
        "strict": True,
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": [
                "status",
                "decision",
                "answer",
                "evidence_refs",
                "next_action",
                "handoff_message",
            ],
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["COMPLETED", "PENDING", "NEEDS_INPUT", "BLOCKED"],
                    "description": "当前角色完成本轮职责后的流程状态。",
                },
                "decision": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 160,
                    "description": "当前角色用自己的话说明本轮实际作出的业务决定。",
                },
                "answer": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 400,
                    "description": "面向当前任务的完整业务结果。",
                },
                "evidence_refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "本轮实际采用的记录编号、上游产物编号或工具返回编号。",
                },
                "next_action": {
                    "type": "string",
                    "maxLength": 200,
                    "description": "当前结果之后的具体业务动作；没有下一步时填写无。",
                },
                "handoff_message": {
                    "type": "string",
                    "maxLength": 240,
                    "description": "交给下游角色的事实、当前处理结果和待办；没有下游时填写无。",
                },
            },
        },
    },
}


def build_user_message(
    step: AgentStepSpec,
    condition: Condition,
    *,
    upstream_override: list[UpstreamArtifact] | None = None,
) -> str:
    if step.raw_user_message is not None:
        return step.raw_user_message
    current_input = step.input_for(condition)
    records = "\n\n".join(
        (
            f"record_id：{record.record_id}\n"
            f"record_type：{record.record_type}\n"
            f"created_at：{record.created_at}\n"
            f"content：{record.content}"
        )
        for record in current_input.records
    ) or "无"
    upstream_items = (
        current_input.upstream_artifacts
        if upstream_override is None
        else upstream_override
    )
    upstream = "\n\n".join(
        _format_artifact(artifact) for artifact in upstream_items
    ) or "无"
    return (
        "【角色】\n"
        f"role_id：{step.role_id}\n"
        f"role_responsibility：{step.role_responsibility}\n\n"
        "【当前任务】\n"
        f"task_id：{step.task_id}\n"
        f"task_content：{step.task_content}\n"
        f"current_time：{step.current_time}\n"
        f"business_object：{step.business_object}\n\n"
        "【可见记录】\n"
        f"{records}\n\n"
        "【上游交接】\n"
        f"{upstream}"
    )


def build_api_tools(tools: list[BusinessToolSpec]) -> list[dict]:
    return [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
                "strict": True,
            },
        }
        for tool in tools
    ]


def final_response_schema() -> dict:
    """Return the inner schema expected by OpenAIClient.generate_chat_turn."""
    return AGENT_BUSINESS_RESULT_SCHEMA["json_schema"]["schema"]


def _format_artifact(artifact: UpstreamArtifact) -> str:
    evidence = "、".join(artifact.evidence_refs) if artifact.evidence_refs else "无"
    return (
        f"artifact_id：{artifact.artifact_id}\n"
        f"producer_role_id：{artifact.producer_role_id}\n"
        f"created_at：{artifact.created_at}\n"
        f"content：{artifact.content}\n"
        f"evidence_refs：{evidence}"
    )
