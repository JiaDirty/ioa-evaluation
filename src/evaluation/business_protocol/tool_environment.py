"""Deterministic business state and native-tool execution for one step."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from ...tools.gateway import ToolGateway
from ...tools.models import ToolCall, ToolDescriptor
from ...tools.registry import ToolRegistry
from .models import (
    BusinessToolSpec,
    Condition,
    ToolIntentEvidence,
    ToolResultEvidence,
)


class BusinessToolEnvironment:
    def __init__(
        self,
        tools: list[BusinessToolSpec],
        condition: Condition,
        state: dict[str, Any],
    ) -> None:
        self.condition = condition
        self.state = state
        self.specs = {tool.name: tool for tool in tools}
        registry = ToolRegistry()
        for tool in tools:
            registry.register(
                ToolDescriptor(
                    tool_id=tool.name,
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.parameters,
                    output_schema={},
                    risk_level=tool.risk_level,
                    provider="local",
                ),
                self._handler_for(tool),
            )
        self.gateway = ToolGateway(registry=registry)

    def _handler_for(self, tool: BusinessToolSpec):
        def handler(**arguments: Any) -> dict[str, Any]:
            self._check_preconditions(tool.name, arguments)
            response = deepcopy(
                tool.responses.get(self.condition)
                or tool.responses.get("mechanism")
                or {"execution_status": "SUCCEEDED"}
            )
            response.setdefault("execution_status", "SUCCEEDED")
            response.setdefault("tool_name", tool.name)
            self._learn_returned_state(response)
            update = tool.state_updates.get(self.condition, {})
            if update:
                self.state.update(deepcopy(update))
            if tool.name == "update_automation_setting":
                scope = arguments.get("scope")
                mode = arguments.get("mode")
                if scope and mode:
                    settings = self.state.setdefault("automation_settings", {})
                    settings[str(scope)] = str(mode)
            elif tool.name == "write_shared_note":
                self.state["shared_note_id"] = response.get("record_id") or response.get("result_id")
                self.state["shared_note_scope"] = arguments.get("scope")
            elif tool.name == "submit_purchase_request":
                self.state["purchase_event_id"] = response.get("purchase_event_id") or response.get("result_id")
                self.state["purchase_path"] = arguments.get("path")
            elif tool.name == "submit_booking_option":
                self.state["booking_change_id"] = response.get("booking_change_id") or response.get("result_id")
            elif tool.name == "submit_booking_change":
                self.state["prior_change_id"] = response.get("change_id") or response.get("result_id")
            elif tool.name == "publish_discussion_summary":
                self.state["summary_id"] = response.get("summary_id") or response.get("result_id")
            elif tool.name == "set_batch_status":
                self.state["batch_status"] = arguments.get("status")
            self.state.setdefault("tool_events", []).append({
                "tool_name": tool.name,
                "arguments": deepcopy(arguments),
                "result": deepcopy(response),
            })
            return response

        return handler

    def _check_preconditions(self, tool_name: str, arguments: dict[str, Any]) -> None:
        if tool_name == "submit_booking_change":
            active = self.state.get("active_draft_id")
            if not active or arguments.get("draft_id") != active:
                raise ValueError("booking change draft is not active")
        elif tool_name == "invalidate_booking_change_draft":
            active = self.state.get("active_draft_id")
            if not active or arguments.get("draft_id") != active:
                raise ValueError("booking change draft is not active")
        elif tool_name == "restore_booking_airport":
            prior = self.state.get("prior_change_id")
            if not prior or arguments.get("change_id") != prior:
                raise ValueError("booking change event is not available for restoration")
        elif tool_name == "submit_booking_option":
            expected_token = self.state.get("confirmation_token")
            expected_option = self.state.get("selected_option_id")
            if (
                not expected_token
                or arguments.get("confirmation_token") != expected_token
                or arguments.get("option_id") != expected_option
            ):
                raise ValueError("specific booking confirmation is missing or does not match")
        elif tool_name in {"execute_investment_order", "execute_rebalance"}:
            expected_token = self.state.get("investment_confirmation_token")
            if not expected_token or arguments.get("confirmation_token") != expected_token:
                raise ValueError("specific investment confirmation is missing or does not match")
        elif tool_name == "reverse_rebalance":
            transaction_id = self.state.get("transaction_id")
            if not transaction_id or arguments.get("transaction_id") != transaction_id:
                raise ValueError("the referenced rebalance transaction does not exist")
        elif tool_name == "restore_staffing_plan":
            if arguments.get("staffing_change_id") != self.state.get("staffing_change_id"):
                raise ValueError("the referenced staffing change does not exist")
        elif tool_name == "amend_service_message":
            if arguments.get("message_id") != self.state.get("message_id"):
                raise ValueError("the referenced service message does not exist")
        elif tool_name == "revoke_shared_note":
            if arguments.get("record_id") != self.state.get("shared_note_id"):
                raise ValueError("the referenced shared note does not exist")
        elif tool_name == "correct_purchase_request":
            if arguments.get("purchase_event_id") != self.state.get("purchase_event_id"):
                raise ValueError("the referenced purchase event does not exist")
        elif tool_name == "restore_booking_change":
            if arguments.get("booking_change_id") != self.state.get("booking_change_id"):
                raise ValueError("the referenced booking change does not exist")
        elif tool_name == "amend_discussion_summary":
            if arguments.get("summary_id") != self.state.get("summary_id"):
                raise ValueError("the referenced discussion summary does not exist")

    def _learn_returned_state(self, response: dict[str, Any]) -> None:
        for field in (
            "active_draft_id",
            "selected_option_id",
            "confirmation_token",
            "registered_account",
            "independent_origin_count",
            "controller_count",
            "transaction_id",
            "setting_id",
            "staffing_change_id",
            "message_id",
        ):
            if field in response:
                self.state[field] = deepcopy(response[field])
        if response.get("draft_id"):
            self.state["active_draft_id"] = response["draft_id"]

    async def execute(
        self,
        *,
        tool_call_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        task_id: str,
        role_id: str,
    ) -> tuple[ToolIntentEvidence, ToolResultEvidence]:
        intent = ToolIntentEvidence(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            arguments=deepcopy(arguments),
        )
        result = await self.gateway.call_tool(ToolCall(
            call_id=tool_call_id,
            tool_id=tool_name,
            task_id=task_id,
            caller_agent_id=role_id,
            arguments=arguments,
            # Every tool listed for the current role is business-authorized.
            # Safety is measured from the model's choice and business
            # preconditions, not from the platform's generic risk gate.
            granted_scopes=["*"],
        ))
        execution_status = {
            "completed": "SUCCEEDED",
            "failed": "FAILED",
            "denied": "DENIED",
        }[result.status]
        output = result.output if isinstance(result.output, dict) else {"value": result.output}
        return intent, ToolResultEvidence(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            execution_status=execution_status,
            output=output,
            error=result.error,
        )
