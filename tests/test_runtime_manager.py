import unittest

from src.runtime import AG2AgentRuntime, AgentInvocation, AgentInvocationResult, AgentRuntime, AgentRuntimeManager


class FakeRuntime(AgentRuntime):
    runtime_type = "fake"

    async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
        return AgentInvocationResult(
            task_id=invocation.task_id,
            trace_id=invocation.trace_id,
            agent_id=invocation.agent_id,
            output={"text": f"ok:{invocation.input.get('task', '')}"},
        )

    def get_card(self):
        return {"agent_id": "a1"}


class FakeAgent:
    def run_task(self, prompt: str, max_turns: int = 1):
        return f"done: {prompt[:20]}:{max_turns}"


class RuntimeManagerTest(unittest.IsolatedAsyncioTestCase):
    async def test_bind_and_invoke_runtime(self):
        manager = AgentRuntimeManager()
        manager.bind_runtime("a1", FakeRuntime(), sub_ioa_id="finance")
        result = await manager.invoke(AgentInvocation(task_id="t", trace_id="tr", agent_id="a1", input={"task": "x"}))
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["text"], "ok:x")

    def test_invoke_sync_text_and_sub_ioa_guard(self):
        manager = AgentRuntimeManager()
        manager.bind_runtime("a1", FakeRuntime(), sub_ioa_id="finance")
        self.assertEqual(manager.invoke_sync_text("finance", "a1", "hello"), "ok:hello")
        with self.assertRaises(ValueError):
            manager.invoke_sync_text("news", "a1", "hello")

    async def test_ag2_runtime_wraps_existing_agent(self):
        runtime = AG2AgentRuntime("a2", {"agent_id": "a2"}, FakeAgent())
        result = await runtime.invoke(
            AgentInvocation(task_id="t", trace_id="tr", agent_id="a2", input={"task": "analyze"}, metadata={"max_turns": 2})
        )
        self.assertEqual(result.status, "completed")
        self.assertIn("done:", result.output["text"])
        self.assertEqual(result.metadata["runtime_type"], "ag2")


if __name__ == "__main__":
    unittest.main()
