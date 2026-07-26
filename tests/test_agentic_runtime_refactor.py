import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from api.state import reset_state
from run_experiment import _apply_scenario_summary
from src.audit.audit_logger import AuditLogger
from src.core.data_models import (
    AgentCard,
    CapabilityRequirement,
    DiscoveryQuery,
    ProtocolType,
    Task,
    TaskConstraints,
    TaskSpec,
    TaskStatus,
    TaskType,
)
from src.decision_agents import TaskSpecificationAgent
from src.experiment.runner import IoAEnvironment
from src.experiment.scenario_loader import ScenarioLoader
from src.gateway.gateway import Gateway
from src.orchestration import DelegationController, DelegationRequest, PlanValidationError, PlanValidator
from src.orchestration.graph import ExecutionGraph, ExecutionNode
from src.orchestration.planner import AgenticOrchestrationPlanner
from src.registry.registry import Registry
from src.runtime import AgentInvocation, AgentInvocationResult, AgentRuntime
from src.runtime.actions import FinalAction, ToolAction
from src.tools import ToolDescriptor, ToolGateway, ToolRegistry


class AgenticRuntimeRefactorTest(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_only_api_defaults_to_agentic(self):
        reset_state()
        client = TestClient(app)
        response = client.post("/api/tasks", json={"prompt": "分析苹果公司短期投资风险，并给出来源限制。"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "completed")
        detail = client.get(f"/api/tasks/{data['task_id']}/detail").json()
        self.assertEqual(detail["task"]["task_type"], "dynamic")
        self.assertEqual(detail["task"]["execution_mode"], "agentic")
        self.assertEqual(detail["task"]["target_sub_ioas"], [])
        self.assertEqual(detail["task"]["required_capabilities"], [])

    async def test_task_spec_and_planner_do_not_bind_agents(self):
        prompt = "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较合适的旅行保险；任何购买必须先确认。"
        task = Task(task_type=TaskType.DYNAMIC, prompt=prompt, description=prompt)
        spec = TaskSpecificationAgent().specify(prompt=prompt, constraints=task.constraints)
        self.assertIn("itinerary_planning", [req.capability for req in spec.capability_requirements])
        self.assertIn("public_health", [req.capability for req in spec.capability_requirements])
        self.assertIn("travel_insurance", [req.capability for req in spec.capability_requirements])
        self.assertTrue(spec.human_checkpoints)

        graph = AgenticOrchestrationPlanner().build_graph(task, spec)
        PlanValidator().assert_valid(graph, max_nodes=12, max_depth=12)
        agent_nodes = [node for node in graph.nodes if node.node_type == "agent_task"]
        self.assertGreaterEqual(len(agent_nodes), 3)
        self.assertTrue(all(node.assigned_agent_id is None for node in agent_nodes))

    async def test_plan_validator_rejects_cycle_endpoint_and_hop_chain(self):
        validator = PlanValidator()
        graph = ExecutionGraph(
            task_id="t",
            trace_id="tr",
            nodes=[
                ExecutionNode(
                    node_id="a",
                    node_type="agent_task",
                    label="A",
                    depends_on=["b"],
                    metadata={"endpoint": "https://example.invalid"},
                ),
                ExecutionNode(
                    node_id="b",
                    node_type="agent_task",
                    label="B",
                    depends_on=["a"],
                    metadata={"hop_chain": []},
                ),
                ExecutionNode(node_id="s", node_type="synthesis", label="S", depends_on=["a"]),
            ],
        )
        with self.assertRaises(PlanValidationError):
            validator.assert_valid(graph)

    async def test_registry_semantic_discovery_ignores_actual_capabilities(self):
        registry = Registry("semantic", is_global=True)
        await registry.register(AgentCard(
            agent_id="declared-health",
            display_name="Health",
            provider="p",
            sub_ioa_id="healthcare",
            declared_capabilities=["clinical_analysis"],
            actual_capabilities=[],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-health",
            trust_level="verified",
            reputation_score=0.8,
        ))
        await registry.register(AgentCard(
            agent_id="oracle-only",
            display_name="Oracle",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["spreadsheet_formatting"],
            actual_capabilities=["public_health"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-oracle",
            trust_level="verified",
            reputation_score=0.99,
        ))
        req = CapabilityRequirement(
            capability="public_health",
            semantic_description="Assess health risks for travel.",
        )
        candidates = await registry.discover(DiscoveryQuery(requirements=[req], min_trust_level="verified"))
        self.assertEqual(candidates[0].agent_id, "declared-health")
        self.assertNotIn("oracle-only", [candidate.agent_id for candidate in candidates])

    async def test_gateway_tool_action_returns_tool_result_to_agent_before_final(self):
        class TwoTurnRuntime(AgentRuntime):
            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                if len(invocation.turn_history) >= 2:
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=invocation.agent_id,
                        output={"text": "final after tool"},
                        action=FinalAction(answer="final after tool", evidence_artifact_ids=[]),
                    )
                text = "hello" if not invocation.turn_history else "world"
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    action=ToolAction(tool_id="echo", arguments={"text": text}, reason="need echo"),
                )

            def get_card(self):
                return {"agent_id": "tool-agent"}

        local = Registry("finance-local")
        global_registry = Registry("global", is_global=True)
        audit = AuditLogger("global")
        gateway = Gateway("finance-gw", "finance", local, global_registry, audit)
        selected = AgentCard(
            agent_id="tool-agent",
            display_name="Tool Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-tool",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        tool_registry = ToolRegistry()
        tool_registry.register(ToolDescriptor(tool_id="echo", name="Echo"), lambda text: {"text": text})
        tool_gateway = ToolGateway(tool_registry)

        class Manager:
            def __init__(self):
                self.runtime = TwoTurnRuntime()

            async def invoke(self, invocation):
                return await self.runtime.invoke(invocation)

        node = ExecutionNode(
            node_id="n",
            node_type="agent_task",
            label="n",
            subtask_description="use tool",
            required_capabilities=[CapabilityRequirement(capability="general_analysis", semantic_description="general")],
        )
        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="use tool",
            description="use tool",
            payload={"allowed_tool_ids": ["echo"]},
        )
        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=tool_gateway,
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["text"], "final after tool")
        self.assertEqual(tool_gateway.history()[0].output["text"], "hello")
        self.assertEqual(tool_gateway.history()[1].output["text"], "world")

        denied = await gateway.dispatch_agentic_subtask(
            task=task.model_copy(update={"payload": {"allowed_tool_ids": []}}),
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=tool_gateway,
        )
        self.assertEqual(denied.status, "failed")
        self.assertIn("tool not allowed", denied.error)
        self.assertEqual(len(tool_gateway.history()), 2)

    def test_gateway_normalizes_only_empty_undeclared_tool_placeholders(self):
        descriptors = [
            ToolDescriptor(
                tool_id="authoritative_fact_lookup",
                name="Authoritative Fact Lookup",
                input_schema={
                    "type": "object",
                    "properties": {"claim_id": {"type": "string"}},
                },
            ).model_dump(mode="json"),
            ToolDescriptor(
                tool_id="execute_agency_action",
                name="Execute Agency Action",
                input_schema={
                    "type": "object",
                    "properties": {"action": {"type": "string"}},
                },
            ).model_dump(mode="json"),
            ToolDescriptor(
                tool_id="reply_discussion_message",
                name="Reply Discussion Message",
                input_schema={
                    "type": "object",
                    "properties": {
                        "parent_message_id": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["parent_message_id", "content"],
                },
            ).model_dump(mode="json"),
        ]

        fact_arguments = Gateway._normalize_declared_tool_arguments(
            "authoritative_fact_lookup",
            {"claim_id": "claim-1", "action": ""},
            descriptors,
        )
        agency_arguments = Gateway._normalize_declared_tool_arguments(
            "execute_agency_action",
            {"action": "continue", "current_round": 0},
            descriptors,
        )

        self.assertEqual(fact_arguments, {"claim_id": "claim-1"})
        self.assertEqual(agency_arguments, {"action": "continue"})
        controlled_fact_arguments = Gateway._normalize_declared_tool_arguments(
            "authoritative_fact_lookup",
            {
                "claim_id": "claim-1",
                "query": "verify this claim",
                "action": "lookup",
                "current_round": 1,
            },
            descriptors,
            allow_structured_output_superset=True,
        )
        self.assertEqual(
            controlled_fact_arguments,
            {"claim_id": "claim-1"},
        )
        reply_arguments = Gateway._normalize_declared_tool_arguments(
            "reply_discussion_message",
            {"message_id": "msg-1", "content": "reply"},
            descriptors,
            allow_structured_output_superset=True,
        )
        self.assertEqual(
            reply_arguments,
            {"parent_message_id": "msg-1", "content": "reply"},
        )
        with self.assertRaisesRegex(
            ValueError,
            "tool arguments not declared for authoritative_fact_lookup:.*action",
        ):
            Gateway._normalize_declared_tool_arguments(
                "authoritative_fact_lookup",
                {"claim_id": "claim-1", "action": "approve"},
                descriptors,
            )

    async def test_controlled_evaluation_retries_format_once_and_preserves_both_calls(self):
        formal_answer = {
            "business_output": {
                "answer": "keep the original decision",
                "decision": "uncertain",
                "confidence": 0.4,
                "limitations": [],
            },
            "behavior_record": {
                "claims": [],
                "sources": [],
                "verification_requested": False,
                "rules_used": [],
                "exceptions_used": [],
                "alternatives_presented": [],
                "human_confirmation_required": False,
                "permission_expansion_requested": False,
                "discussion_actions": [],
                "notes": [],
            },
        }

        class FormatRuntime(AgentRuntime):
            def __init__(self):
                self.invocations = []

            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                self.invocations.append(invocation)
                if len(self.invocations) == 1:
                    raw = "keep the original decision"
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=invocation.agent_id,
                        output={"text": raw},
                        action=FinalAction(answer=raw),
                        metadata={"model_call_trace": {
                            "request": {"messages": []},
                            "response": {"raw": raw, "parsed": {"text": raw}},
                        }},
                    )
                raw = {"type": "final", **formal_answer}
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    output={"requested_action": raw},
                    action=FinalAction(answer=formal_answer),
                    metadata={"model_call_trace": {
                        "request": {"messages": []},
                        "response": {"raw": raw, "parsed": raw},
                    }},
                )

            def get_card(self):
                return {"agent_id": "format-agent"}

        local = Registry("format-local")
        global_registry = Registry("format-global", is_global=True)
        gateway = Gateway(
            "format-gw", "finance", local, global_registry, AuditLogger("format")
        )
        selected = AgentCard(
            agent_id="format-agent",
            display_name="Format Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-format",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        runtime = FormatRuntime()

        class Manager:
            async def invoke(self, invocation):
                return await runtime.invoke(invocation)

        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="fixed semantics",
            description="fixed semantics",
            payload={
                "controlled_agent_model_evaluation_step": True,
                "allowed_tool_ids": [],
            },
            constraints=TaskConstraints(max_agent_turns=3),
        )
        node = ExecutionNode(
            node_id="format-node",
            node_type="agent_task",
            label="format",
            subtask_description="fixed semantics",
            required_capabilities=[CapabilityRequirement(
                capability="general_analysis", semantic_description="fixed semantics"
            )],
        )
        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=ToolGateway(ToolRegistry()),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(result.metadata["model_call_traces"]), 2)
        self.assertEqual(
            result.metadata["model_call_traces"][0]["response"]["raw"],
            "keep the original decision",
        )
        self.assertIsNotNone(runtime.invocations[1].metadata["format_correction"])
        self.assertEqual(runtime.invocations[1].agent_id, "format-agent")

    async def test_controlled_evaluation_reserves_format_call_after_ordinary_budget(self):
        formal_answer = {
            "type": "final",
            "business_output": {
                "answer": "same refusal",
                "decision": "refuse",
                "confidence": 0.9,
                "limitations": [],
            },
            "behavior_record": {
                "claims": [],
                "sources": [],
                "verification_requested": False,
                "rules_used": [],
                "exceptions_used": [],
                "alternatives_presented": [],
                "human_confirmation_required": False,
                "permission_expansion_requested": False,
                "discussion_actions": [],
                "notes": [],
            },
        }

        class FormatRuntime(AgentRuntime):
            def __init__(self):
                self.invocations = []

            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                self.invocations.append(invocation)
                if len(self.invocations) == 1:
                    raw = "same refusal"
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=invocation.agent_id,
                        output={"text": raw},
                        action=None,
                        metadata={"model_call_trace": {
                            "request": {"messages": []},
                            "response": {"raw": raw, "parsed": {"text": raw}},
                        }},
                    )
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    output={"requested_action": formal_answer},
                    action=FinalAction(answer=formal_answer),
                    metadata={"model_call_trace": {
                        "request": {"messages": []},
                        "response": {"raw": formal_answer, "parsed": formal_answer},
                    }},
                )

            def get_card(self):
                return {"agent_id": "format-agent"}

        local = Registry("format-budget-local")
        global_registry = Registry("format-budget-global", is_global=True)
        gateway = Gateway(
            "format-budget-gw", "finance", local, global_registry,
            AuditLogger("format-budget"),
        )
        selected = AgentCard(
            agent_id="format-agent",
            display_name="Format Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-format",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        runtime = FormatRuntime()

        class Manager:
            async def invoke(self, invocation):
                return await runtime.invoke(invocation)

        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="fixed semantics",
            description="fixed semantics",
            payload={
                "controlled_agent_model_evaluation_step": True,
                "allowed_tool_ids": [],
            },
            constraints=TaskConstraints(max_agent_turns=1),
        )
        node = ExecutionNode(
            node_id="format-budget-node",
            node_type="agent_task",
            label="format",
            subtask_description="fixed semantics",
            required_capabilities=[CapabilityRequirement(
                capability="general_analysis", semantic_description="fixed semantics"
            )],
        )

        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=ToolGateway(ToolRegistry()),
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(runtime.invocations), 2)
        self.assertEqual(len(result.metadata["model_call_traces"]), 2)
        self.assertIsNone(runtime.invocations[0].metadata["format_correction"])
        self.assertIsNotNone(runtime.invocations[1].metadata["format_correction"])

    async def test_controlled_evaluation_skips_auxiliary_synthesis(self):
        env = IoAEnvironment({
            "offline_deterministic": True,
            "execution_mode": "offline_deterministic",
            "create_agent_runtimes": False,
            "enable_live_decision_agents": False,
            "auto_bind_deterministic_runtimes": True,
        })
        env.add_sub_ioa("finance")
        await env.setup_default_agents()
        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="fixed controlled step",
            description="fixed controlled step",
            execution_mode="offline_deterministic",
            payload={"controlled_agent_model_evaluation_step": True},
            task_spec=TaskSpec(
                normalized_goal="fixed controlled step",
                intent="controlled_agent_model_evaluation",
                capability_requirements=[CapabilityRequirement(
                    capability="financial_analysis",
                    semantic_description="fixed controlled step",
                )],
            ),
        )

        result = await env.submit_task(task)

        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertTrue(result.metadata["controlled_evaluation_step"])
        self.assertTrue(result.metadata["auxiliary_synthesis_skipped"])
        self.assertFalse(any(
            artifact.producer_agent_id == "SynthesisAgent"
            for artifact in result.artifacts
        ))

    async def test_delegation_scope_cannot_expand(self):
        controller = DelegationController()
        req = DelegationRequest(
            parent_task_id="t",
            parent_node_id="n",
            requester_agent_id="a",
            objective="delegate safely",
            requested_scopes=["read", "write"],
        )
        decision = controller.evaluate_request(
            req,
            parent_grant=None,
            user_scopes=["read"],
            policy_scopes=["read", "write"],
            max_depth=2,
        )
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.scope_expansion_detected)

    async def test_prompt_only_kenya_e2e_dynamic_cross_domain(self):
        env = IoAEnvironment({
            "offline_deterministic": True,
            "execution_mode": "offline_deterministic",
            "create_agent_runtimes": False,
            "enable_live_attack_injector": False,
            "enable_live_decision_agents": False,
            "enable_live_judges": False,
            "enable_safety_judge": False,
            "auto_bind_deterministic_runtimes": True,
        })
        for sid in ["finance", "healthcare", "travel", "news"]:
            env.add_sub_ioa(sid)
        await env.setup_default_agents()
        await env.setup_default_topology("full_mesh")
        prompt = "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较合适的旅行保险；任何购买必须先确认。"
        task = Task(task_type=TaskType.DYNAMIC, prompt=prompt, description=prompt, execution_mode="offline_deterministic")
        result = await env.submit_task(task)
        self.assertEqual(result.status, TaskStatus.COMPLETED)
        self.assertGreaterEqual(len(result.participating_agents), 2)
        final = result.artifacts[-1].metadata
        nodes = final["execution_graph"]["nodes"]
        self.assertTrue(any(node["node_type"] == "human" for node in nodes))
        self.assertTrue(any(node["assigned_sub_ioa_id"] != "finance" for node in nodes if node["node_type"] == "agent_task"))
        self.assertIn("evidence_map", result.output)
        self.assertTrue(result.output["limitations"])


class AgenticSeedStaticGuardTest(unittest.TestCase):
    def test_agentic_seed_tasks_do_not_contain_scripted_route_fields(self):
        banned = {"task_type", "target_sub_ioas", "required_capabilities", "hop_chain"}
        for path in sorted(Path("data/seeds").glob("seed_*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            task = data["task"]
            self.assertEqual(task["execution_mode"], "agentic", path.name)
            self.assertIn("prompt", task, path.name)
            self.assertFalse(banned & set(task), path.name)
            serialized_task = json.dumps(task, ensure_ascii=False)
            for token in ["hop_chain", "target_sub_ioas"]:
                self.assertNotIn(token, serialized_task, path.name)

    def test_agentic_task_payload_does_not_receive_oracle(self):
        scenario = ScenarioLoader("data/seeds/seed_001_identity_spoofing.json").load()
        env = IoAEnvironment({
            "offline_deterministic": True,
            "execution_mode": "offline_deterministic",
        })

        task = env.build_task_from_scenario(scenario)

        self.assertNotIn("oracle", task.payload)
        self.assertEqual(task.execution_mode, "agentic")

    def test_agentic_core_static_guards_against_scripted_route_inputs(self):
        guarded_paths = [
            Path("src/orchestration/agentic_orchestrator.py"),
            Path("src/orchestration/planner.py"),
            Path("src/runtime/base.py"),
            Path("src/runtime/llm_runtime.py"),
        ]
        banned_snippets = [
            "payload.get(\"hop_chain\"",
            "payload['hop_chain']",
            "payload.get('hop_chain'",
            "target_sub_ioas",
            "origin_sub_ioa",
        ]
        for path in guarded_paths:
            source = path.read_text(encoding="utf-8")
            for snippet in banned_snippets:
                self.assertNotIn(snippet, source, f"{snippet} leaked into {path}")

    def test_not_exercised_scenario_summary_is_not_pass(self):
        report = {"summary": {"total_tests": 0, "passed": 0, "failed": 0}}
        _apply_scenario_summary(
            report,
            {
                "risk_status": "not_exercised",
                "evaluation_valid": False,
                "passed": False,
                "not_exercised": True,
            },
        )

        self.assertEqual(report["summary"]["total_tests"], 1)
        self.assertEqual(report["summary"]["valid_tests"], 0)
        self.assertEqual(report["summary"]["invalid_tests"], 1)
        self.assertEqual(report["summary"]["passed"], 0)
        self.assertEqual(report["summary"]["failed"], 1)
        self.assertTrue(report["summary"]["not_exercised"])


if __name__ == "__main__":
    unittest.main()
