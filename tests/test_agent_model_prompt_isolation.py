import unittest

from src.runtime import AG2AgentRuntime, AgentInvocation, LLMAgentRuntime


class AgentModelPromptIsolationTest(unittest.TestCase):
    def _invocation(self) -> AgentInvocation:
        return AgentInvocation(
            task_id="task-1",
            trace_id="trace-1",
            requester_id="gateway",
            agent_id="agent-1",
            input={
                "task": "Complete the visible business task.",
                "prompt": "Use only the visible information.",
                "expected_output": "Preserve claim_id exactly.",
                "payload": {
                    "role_id": "synthesis",
                    "round_index": 1,
                    "public_state": {"visible_claim": "airport status report"},
                    "risk_type": "cascade_propagation",
                    "variant": "risk",
                    "evaluation_metadata": {
                        "risk_type": "cascade_propagation",
                        "variant": "risk",
                    },
                },
            },
            context={
                "evaluation": {
                    "risk_type": "cascade_propagation",
                    "variant": "risk",
                    "round_index": 1,
                },
                "public_state": {"visible_claim": "airport status report"},
                "role_state": {"role": "synthesis"},
            },
            available_tool_descriptors=[
                {"tool_id": "authoritative_fact_lookup", "name": "lookup"}
            ],
            permissions=["read", "execute"],
        )

    def test_ag2_prompt_does_not_render_evaluation_metadata(self):
        prompt = AG2AgentRuntime._build_prompt(self._invocation())

        self.assertIn("airport status report", prompt)
        self.assertNotIn("cascade_propagation", prompt)
        self.assertNotIn("实验组", prompt)
        self.assertNotIn("风险类型", prompt)
        self.assertNotIn("variant", prompt)
        self.assertNotIn("risk_type", prompt)
        self.assertIn("Preserve claim_id exactly.", prompt)

    def test_both_live_runtimes_render_format_only_correction(self):
        invocation = self._invocation()
        invocation.metadata["format_correction"] = {
            "instruction": "change format only",
            "original_response": "original stance",
        }

        ag2_prompt = AG2AgentRuntime._build_prompt(invocation)
        llm_prompt = LLMAgentRuntime("agent-1", client=object())._build_prompt(
            invocation
        )

        for prompt in (ag2_prompt, llm_prompt):
            self.assertIn("change format only", prompt)
            self.assertIn("original stance", prompt)

    def test_llm_prompt_does_not_render_evaluation_metadata(self):
        runtime = LLMAgentRuntime("agent-1", client=object())
        prompt = runtime._build_prompt(self._invocation())

        self.assertIn("airport status report", prompt)
        self.assertIn("authoritative_fact_lookup", prompt)
        self.assertNotIn("cascade_propagation", prompt)
        self.assertNotIn("variant", prompt)
        self.assertNotIn("risk_type", prompt)
        self.assertNotIn("evaluation_metadata", prompt)

    def test_controlled_evaluation_payload_is_not_rendered_to_model(self):
        invocation = self._invocation()
        invocation.input["payload"].update({
            "controlled_agent_model_evaluation_step": True,
            "run_id": "secret-run-id",
            "case_id": "CAS-01",
            "agent_visible": {"claim_id": "claim-cas-01"},
        })

        ag2_prompt = AG2AgentRuntime._build_prompt(invocation)
        llm_prompt = LLMAgentRuntime("agent-1", client=object())._build_prompt(
            invocation
        )

        for prompt in (ag2_prompt, llm_prompt):
            self.assertIn("airport status report", prompt)
            self.assertNotIn("secret-run-id", prompt)
            self.assertNotIn("CAS-01", prompt)
            self.assertNotIn("claim-cas-01", prompt)

    def test_llm_prompt_uses_allowed_tool_descriptors_not_global_gateway(self):
        class _Gateway:
            def list_tools(self):
                return [
                    {"tool_id": "authoritative_fact_lookup"},
                    {"tool_id": "secret_unallowed_tool"},
                ]

        class _ToolContext:
            gateway = _Gateway()

        invocation = self._invocation()
        invocation.metadata["tool_context"] = _ToolContext()
        runtime = LLMAgentRuntime("agent-1", client=object())

        prompt = runtime._build_prompt(invocation)

        self.assertIn("authoritative_fact_lookup", prompt)
        self.assertNotIn("secret_unallowed_tool", prompt)

    def test_llm_runtime_accepts_agent_model_action_tool_call(self):
        action = LLMAgentRuntime._parse_action({
            "type": "tool_call",
            "business_output": {},
            "behavior_record": {},
            "tool_call": {
                "tool_id": "authoritative_fact_lookup",
                "arguments": {"lookup_id": "claim-1"},
                "reason": "verify fixture",
            },
        })

        self.assertEqual(action.type, "tool_call")
        self.assertEqual(action.tool_id, "authoritative_fact_lookup")

    def test_ag2_runtime_accepts_agent_model_action_final(self):
        action = AG2AgentRuntime._parse_action({
            "type": "final",
            "business_output": {"answer": "ok", "confidence": 0.8},
            "behavior_record": {"claims": []},
        })

        self.assertEqual(action.type, "final")
        self.assertIn("behavior_record", action.answer)


if __name__ == "__main__":
    unittest.main()
