import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.ioa_agent import IoAAgent
from src.audit.audit_logger import AuditLogger
from src.core.data_models import (
    AgentCard,
    CapabilityRequirement,
    ProtocolType,
    Task,
    TaskConstraints,
    TaskType,
)
from src.gateway.gateway import Gateway
from src.llm.client import OpenAIClient
from src.llm.config import AgentLLMConfig
from src.orchestration.graph import ExecutionNode
from src.registry.registry import Registry
from src.runtime.ag2_runtime import AG2AgentRuntime
from src.runtime.base import AgentInvocation
from src.runtime.llm_runtime import LLMAgentRuntime
from src.tools.gateway import ToolGateway
from src.tools.models import ToolDescriptor
from src.tools.registry import ToolRegistry


FINAL_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "decision": {"type": "string"},
        "answer": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "next_action": {"type": "string"},
        "handoff_message": {"type": "string"},
    },
    "required": [
        "status",
        "decision",
        "answer",
        "evidence_refs",
        "next_action",
        "handoff_message",
    ],
    "additionalProperties": False,
}


class _ToolCallCompletions:
    def __init__(self):
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        function = SimpleNamespace(
            name="query_business_status",
            arguments='{"claim_id":"记录-01"}',
        )
        message = SimpleNamespace(
            content=None,
            refusal=None,
            tool_calls=[SimpleNamespace(
                id="call-native-1",
                type="function",
                function=function,
            )],
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="tool_calls")],
            usage=None,
            id="response-native-1",
            model="fixture-model",
            created=1,
            system_fingerprint=None,
        )


class _NativeIoAAgent:
    structured_output_schema = "AgentDecisionOutput"
    model = "fixture-model"
    system_message = "system"
    last_usage = None
    last_retry_count = 0
    last_response_metadata = None

    def __init__(self):
        self.calls = []
        self.last_provider_calls = []

    def run_task(self, task, max_turns=1, model_request_config=None):
        raise AssertionError("native evaluation must not use the AG2 text task path")

    def run_provider_turn(
        self,
        messages,
        *,
        tools,
        tool_choice,
        parallel_tool_calls,
        response_format,
        model_request_config,
    ):
        self.calls.append({
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": parallel_tool_calls,
            "response_format": response_format,
            "model_request_config": model_request_config,
        })
        if len(self.calls) == 1:
            tool_call = {
                "id": "call-native-1",
                "type": "function",
                "function": {
                    "name": "query_business_status",
                    "arguments": '{"claim_id":"记录-01"}',
                },
            }
            turn = {
                "content": None,
                "tool_calls": [tool_call],
                "finish_reason": "tool_calls",
                "assistant_message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tool_call],
                },
            }
        else:
            turn = {
                "content": json.dumps({
                    "status": "COMPLETED",
                    "decision": "采用查询结果",
                    "answer": "记录有效",
                    "evidence_refs": ["记录-01"],
                    "next_action": "完成交接",
                    "handoff_message": "记录-01 已查询",
                }, ensure_ascii=False),
                "tool_calls": [],
                "finish_reason": "stop",
                "assistant_message": {
                    "role": "assistant",
                    "content": "final",
                },
            }
        self.last_provider_calls = [{
            "request": {
                "messages": messages,
                "tools": tools,
                "tool_choice": tool_choice,
                "parallel_tool_calls": parallel_tool_calls,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"strict": True, "schema": response_format},
                },
            },
            "response": turn,
            "error": None,
        }]
        return turn


class NativeToolProtocolTest(unittest.IsolatedAsyncioTestCase):
    def test_openai_client_sends_and_preserves_native_tool_call(self):
        client = OpenAIClient(AgentLLMConfig(
            api_key="test-key",
            model="fixture-model",
        ))
        completions = _ToolCallCompletions()
        client.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )
        tools = [{
            "type": "function",
            "function": {
                "name": "query_business_status",
                "description": "查询业务状态",
                "parameters": {
                    "type": "object",
                    "properties": {"claim_id": {"type": "string"}},
                    "required": ["claim_id"],
                    "additionalProperties": False,
                },
                "strict": True,
            },
        }]

        turn = client.generate_chat_turn(
            [{"role": "user", "content": "查询记录"}],
            tools=tools,
            tool_choice="auto",
            parallel_tool_calls=False,
            response_format=FINAL_SCHEMA,
        )

        self.assertEqual(completions.kwargs["tools"], tools)
        self.assertEqual(completions.kwargs["tool_choice"], "auto")
        self.assertFalse(completions.kwargs["parallel_tool_calls"])
        self.assertEqual(turn["tool_calls"][0]["id"], "call-native-1")
        self.assertEqual(
            turn["tool_calls"][0]["function"]["name"],
            "query_business_status",
        )

    def test_ioa_agent_native_turn_forwards_only_provider_fields(self):
        response = _ToolCallCompletions().create()

        class FakeWrapper:
            last_usage = {"total_tokens": 10}
            last_retry_count = 0
            last_response_metadata = {"finish_reason": "tool_calls"}
            provider_call_records = [{"request": {}, "response": {}}]

            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return response

        wrapper = FakeWrapper()
        agent = IoAAgent(
            sub_ioa_id="travel",
            agent=SimpleNamespace(name="fixture-agent"),
            user_proxy=SimpleNamespace(),
            config={},
            llm_config={"config_list": [{"model": "fixture-model"}]},
            system_message="system",
        )
        tools = [{
            "type": "function",
            "function": {
                "name": "query_business_status",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

        with patch(
            "src.agents.ioa_agent._checked_ag2_client",
            return_value=wrapper,
        ):
            turn = agent.run_provider_turn(
                [{"role": "user", "content": "query"}],
                tools=tools,
                tool_choice="auto",
                response_format=FINAL_SCHEMA,
                model_request_config={
                    "temperature": 0.2,
                    "retry_count": 3,
                    "retry_delay": 0.5,
                },
            )

        self.assertEqual(wrapper.kwargs["tools"], tools)
        self.assertEqual(wrapper.kwargs["tool_choice"], "auto")
        self.assertNotIn("retry_count", wrapper.kwargs)
        self.assertNotIn("retry_delay", wrapper.kwargs)
        self.assertEqual(
            wrapper.kwargs["response_format"]["type"], "json_schema"
        )
        self.assertEqual(turn["tool_calls"][0]["id"], "call-native-1")
        self.assertEqual(agent.last_usage["total_tokens"], 10)

    async def test_llm_runtime_normalizes_native_tool_call_without_executing_it(self):
        class NativeClient:
            model = "fixture-model"
            temperature = 0.2
            last_provider_calls = []
            last_usage = None
            last_response_metadata = {"finish_reason": "tool_calls"}

            def __init__(self):
                self.messages = None
                self.kwargs = None

            def generate_chat_turn(self, messages, **kwargs):
                self.messages = messages
                self.kwargs = kwargs
                tool_call = {
                    "id": "call-runtime-1",
                    "type": "function",
                    "function": {
                        "name": "query_business_status",
                        "arguments": '{"claim_id":"记录-01"}',
                    },
                }
                return {
                    "content": None,
                    "tool_calls": [tool_call],
                    "finish_reason": "tool_calls",
                    "assistant_message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [tool_call],
                    },
                }

        client = NativeClient()
        runtime = LLMAgentRuntime(
            "native-llm",
            client,
            system_prompt="system",
        )
        result = await runtime.invoke(AgentInvocation(
            task_id="task-native",
            trace_id="trace-native",
            agent_id="native-llm",
            input={"task": "查询记录"},
            available_tool_descriptors=[{
                "tool_id": "query_business_status",
                "name": "业务状态查询",
                "description": "查询记录",
                "input_schema": {
                    "type": "object",
                    "properties": {"claim_id": {"type": "string"}},
                    "required": ["claim_id"],
                    "additionalProperties": False,
                },
            }],
            metadata={
                "agentic_loop": True,
                "native_tool_protocol": True,
                "visible_action_schema": FINAL_SCHEMA,
            },
        ))

        self.assertEqual(result.action.type, "tool_call")
        self.assertEqual(result.action.tool_id, "query_business_status")
        self.assertEqual(result.action.arguments, {"claim_id": "记录-01"})
        self.assertEqual(client.messages[0]["role"], "system")
        self.assertEqual(
            client.kwargs["tools"][0]["function"]["name"],
            "query_business_status",
        )
        self.assertEqual(
            result.metadata["provider_tool_call"]["id"], "call-runtime-1"
        )

    async def test_gateway_replays_assistant_tool_call_and_matching_tool_result(self):
        local = Registry("native-local")
        global_registry = Registry("native-global", is_global=True)
        gateway = Gateway(
            "native-gw",
            "travel",
            local,
            global_registry,
            AuditLogger("native-audit"),
        )
        selected = AgentCard(
            agent_id="native-agent",
            display_name="Native Agent",
            provider="fixture",
            sub_ioa_id="travel",
            declared_capabilities=["business_lookup"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-native",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        descriptor = ToolDescriptor(
            tool_id="query_business_status",
            name="业务状态查询",
            description="按记录编号查询状态。",
            input_schema={
                "type": "object",
                "properties": {"claim_id": {"type": "string"}},
                "required": ["claim_id"],
                "additionalProperties": False,
            },
        )
        registry = ToolRegistry()
        registry.register(
            descriptor,
            lambda claim_id: {
                "claim_id": claim_id,
                "status": "valid",
            },
        )
        tool_gateway = ToolGateway(registry)
        ioa_agent = _NativeIoAAgent()
        runtime = AG2AgentRuntime("native-agent", selected, ioa_agent)

        class Manager:
            async def invoke(self, invocation):
                return await runtime.invoke(invocation)

        task = Task(
            task_type=TaskType.DYNAMIC,
            description="查询记录并交接结果",
            execution_mode="agentic_live",
            payload={
                "controlled_agent_model_evaluation_step": True,
                "allowed_tool_ids": ["query_business_status"],
                "tool_descriptors": [descriptor.model_dump(mode="json")],
                "visible_action_schema": FINAL_SCHEMA,
                "final_action_schema": FINAL_SCHEMA,
                "model_request_config": {"temperature": 0.2},
            },
            user_grants=["read", "execute"],
            constraints=TaskConstraints(
                max_agent_turns=2,
                max_total_tool_calls=1,
            ),
        )
        node = ExecutionNode(
            node_id="native-node",
            node_type="agent_task",
            label="native",
            subtask_description="查询记录并交接结果",
            required_capabilities=[CapabilityRequirement(
                capability="business_lookup",
                semantic_description="查询业务记录",
            )],
        )

        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=tool_gateway,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(ioa_agent.calls), 2)
        first, second = ioa_agent.calls
        self.assertEqual(first["tool_choice"], "auto")
        self.assertEqual(second["tool_choice"], "none")
        self.assertTrue(first["tools"][0]["function"]["strict"])
        self.assertEqual(
            [message["role"] for message in second["messages"]],
            ["system", "user", "assistant", "tool"],
        )
        self.assertEqual(
            second["messages"][2]["tool_calls"][0]["id"],
            "call-native-1",
        )
        self.assertEqual(
            second["messages"][3]["tool_call_id"],
            "call-native-1",
        )
        tool_content = json.loads(second["messages"][3]["content"])
        self.assertEqual(tool_content["status"], "completed")
        self.assertEqual(tool_content["output"]["claim_id"], "记录-01")


if __name__ == "__main__":
    unittest.main()
