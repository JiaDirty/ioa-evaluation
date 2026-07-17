import unittest

from src.runtime import AgentInvocation, AgentRuntimeManager, LLMAgentRuntime
from src.tools import ToolDescriptor, ToolGateway, ToolRegistry


class FakeToolCallClient:
    def generate_json(self, system, user):
        return {
            "type": "tool_call",
            "tool_id": "echo",
            "arguments": {"text": "hello"},
        }


class FakeDeniedToolCallClient:
    def generate_json(self, system, user):
        return {
            "type": "tool_call",
            "tool_id": "write",
            "arguments": {"text": "blocked"},
        }


class LLMAgentRuntimeToolGatewayTest(unittest.IsolatedAsyncioTestCase):
    async def test_llm_runtime_calls_tool_gateway_for_structured_tool_call(self):
        registry = ToolRegistry()
        registry.register(ToolDescriptor(tool_id="echo", name="Echo"), lambda text: {"text": text})
        manager = AgentRuntimeManager(ToolGateway(registry))
        manager.bind_runtime("llm", LLMAgentRuntime("llm", FakeToolCallClient()))

        result = await manager.invoke(AgentInvocation(task_id="t", trace_id="tr", agent_id="llm"))

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["tool_result"]["text"], "hello")
        self.assertEqual(result.tool_calls[0]["status"], "completed")

    async def test_llm_runtime_surfaces_denied_tool_result(self):
        registry = ToolRegistry()
        registry.register(ToolDescriptor(tool_id="write", name="Write", required_scopes=["write"]))
        manager = AgentRuntimeManager(ToolGateway(registry))
        manager.bind_runtime("llm", LLMAgentRuntime("llm", FakeDeniedToolCallClient()))

        result = await manager.invoke(AgentInvocation(task_id="t", trace_id="tr", agent_id="llm"))

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.tool_calls[0]["status"], "denied")
        self.assertIn("missing tool scopes", result.error)


if __name__ == "__main__":
    unittest.main()
