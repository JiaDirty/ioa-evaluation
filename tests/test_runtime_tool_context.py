import unittest

from src.runtime import AgentInvocation, AgentInvocationResult, AgentRuntime, AgentRuntimeManager
from src.tools import ToolDescriptor, ToolExecutionContext, ToolGateway, ToolRegistry


class ToolUsingRuntime(AgentRuntime):
    runtime_type = "tool-using"

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        tool_context = invocation.metadata.get("tool_context")
        result = await tool_context.call_tool("echo", {"text": "hi"})
        return AgentInvocationResult(
            task_id=invocation.task_id,
            trace_id=invocation.trace_id,
            agent_id=invocation.agent_id,
            output={"status": result.status, "value": result.output},
            tool_calls=[result.model_dump(mode="json")],
        )

    def get_card(self):
        return {"agent_id": "a1"}


class RuntimeToolContextTest(unittest.IsolatedAsyncioTestCase):
    async def test_manager_injects_tool_execution_context(self):
        registry = ToolRegistry()
        registry.register(ToolDescriptor(tool_id="echo", name="Echo"), lambda text: {"text": text})
        gateway = ToolGateway(registry)
        manager = AgentRuntimeManager(gateway)
        manager.bind_runtime("a1", ToolUsingRuntime())

        result = await manager.invoke(AgentInvocation(task_id="t", trace_id="tr", agent_id="a1"))

        self.assertEqual(result.output["status"], "completed")
        self.assertEqual(result.output["value"]["text"], "hi")
        self.assertEqual(result.tool_calls[0]["tool_id"], "echo")

    async def test_context_uses_invocation_permissions_for_gateway_policy(self):
        registry = ToolRegistry()
        registry.register(ToolDescriptor(tool_id="secret", name="Secret", required_scopes=["secret"]))
        gateway = ToolGateway(registry)
        context = ToolExecutionContext(gateway, task_id="t", trace_id="tr", agent_id="a1", granted_scopes=[])

        result = await context.call_tool("secret", {})

        self.assertEqual(result.status, "denied")


if __name__ == "__main__":
    unittest.main()
