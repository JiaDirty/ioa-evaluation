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
from src.gateway.gateway import Gateway, _agent_visible_tool_result
from src.orchestration import DelegationController, DelegationRequest, PlanValidationError, PlanValidator
from src.orchestration.graph import ExecutionGraph, ExecutionNode
from src.orchestration.planner import AgenticOrchestrationPlanner
from src.registry.registry import Registry
from src.runtime import AgentInvocation, AgentInvocationResult, AgentRuntime
from src.runtime.actions import FinalAction, ToolAction
from src.tools import ToolDescriptor, ToolGateway, ToolRegistry


class AgenticRuntimeRefactorTest(unittest.IsolatedAsyncioTestCase):
    def test_agent_visible_tool_result_removes_audit_only_fields(self):
        raw = {
            "call_id": "internal-call",
            "tool_id": "query_business_status",
            "status": "completed",
            "output": {
                "answer": "可见结论",
                "query_key": "RUM:RUM-01:rumor_status",
                "fixture_hit": True,
                "semantic_success": True,
                "nested": {
                    "controller_group": "coordinated",
                    "target_controller_group": "independent",
                    "content": "可见内容",
                },
            },
            "metadata": {"trace_id": "internal-trace"},
        }

        visible = _agent_visible_tool_result(raw)

        self.assertEqual(visible["tool_id"], "query_business_status")
        self.assertEqual(visible["output"]["answer"], "可见结论")
        self.assertEqual(visible["output"]["nested"], {"content": "可见内容"})
        serialized = json.dumps(visible, ensure_ascii=False)
        for forbidden in (
            "RUM-01", "query_key", "fixture_hit", "semantic_success",
            "controller_group", "call_id", "metadata", "trace_id",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_valid_agent_model_json_string_does_not_require_format_correction(self):
        payload = {
            "type": "final",
            "business_output": {
                "answer": "keep the decision",
                "decision": "keep",
                "confidence": 0.9,
                "limitations": [],
            },
            "behavior_record": {},
            "tool_call": None,
            "reason": "",
        }
        trace = {
            "response": {
                "raw": json.dumps(payload),
                "parsed": json.dumps(payload),
            }
        }

        self.assertTrue(Gateway._has_valid_agent_model_action(trace))

    def test_direct_six_field_final_is_valid_gateway_wire_output(self):
        payload = {
            "status": "COMPLETED",
            "decision": "保持当前业务安排。",
            "answer": "当前步骤已完成。",
            "evidence_refs": ["记录-01"],
            "next_action": "完成本步骤",
            "handoff_message": "",
        }
        trace = {
            "response": {
                "raw": json.dumps(payload, ensure_ascii=False),
                "parsed": json.dumps(payload, ensure_ascii=False),
            }
        }

        self.assertTrue(Gateway._has_valid_agent_model_action(trace))

    async def test_valid_agent_model_json_string_completes_in_one_gateway_call(self):
        payload = {
            "type": "final",
            "business_output": {
                "answer": "keep the decision",
                "decision": "keep",
                "confidence": 0.9,
                "limitations": [],
            },
            "behavior_record": {},
            "tool_call": None,
            "reason": "",
        }

        class JsonStringRuntime(AgentRuntime):
            def __init__(self):
                self.invocations = []

            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                self.invocations.append(invocation)
                serialized = json.dumps(payload)
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    action=FinalAction(answer=payload),
                    metadata={"model_call_trace": {
                        "request": {"messages": []},
                        "response": {"raw": serialized, "parsed": serialized},
                    }},
                )

            def get_card(self):
                return {"agent_id": "json-string-agent"}

        runtime = JsonStringRuntime()

        class Manager:
            async def invoke(self, invocation):
                return await runtime.invoke(invocation)

        gateway = Gateway(
            "json-string-gw",
            "finance",
            Registry("json-string-local"),
            Registry("json-string-global", is_global=True),
            AuditLogger("json-string"),
        )
        selected = AgentCard(
            agent_id="json-string-agent",
            display_name="JSON String Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-json-string",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="fixed semantics",
            description="fixed semantics",
            payload={
                "controlled_agent_model_evaluation_step": True,
                "allowed_tool_ids": [],
            },
        )
        node = ExecutionNode(
            node_id="json-string-node",
            node_type="agent_task",
            label="json string",
            subtask_description="fixed semantics",
            required_capabilities=[CapabilityRequirement(
                capability="general_analysis",
                semantic_description="fixed semantics",
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
        self.assertEqual(len(runtime.invocations), 1)
        self.assertIsNone(runtime.invocations[0].metadata["format_correction"])
        self.assertEqual(len(result.metadata["model_call_traces"]), 1)

    async def test_runtime_connection_error_is_not_sent_to_format_correction(self):
        class ConnectionFailureRuntime(AgentRuntime):
            def __init__(self):
                self.invocations = []

            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                self.invocations.append(invocation)
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    status="failed",
                    error="Connection error.",
                    metadata={"model_call_trace": {
                        "request": {"messages": []},
                        "response": {
                            "raw": None,
                            "parsed": None,
                            "error": "Connection error.",
                        },
                    }},
                )

            def get_card(self):
                return {"agent_id": "connection-agent"}

        runtime = ConnectionFailureRuntime()

        class Manager:
            async def invoke(self, invocation):
                return await runtime.invoke(invocation)

        gateway = Gateway(
            "connection-gw",
            "finance",
            Registry("connection-local"),
            Registry("connection-global", is_global=True),
            AuditLogger("connection"),
        )
        selected = AgentCard(
            agent_id="connection-agent",
            display_name="Connection Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-connection",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
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
            node_id="connection-node",
            node_type="agent_task",
            label="connection",
            subtask_description="fixed semantics",
            required_capabilities=[CapabilityRequirement(
                capability="general_analysis",
                semantic_description="fixed semantics",
            )],
        )

        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=ToolGateway(ToolRegistry()),
        )

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.error, "Connection error.")
        self.assertEqual(len(runtime.invocations), 1)
        self.assertIsNone(runtime.invocations[0].metadata["format_correction"])
        self.assertEqual(len(result.metadata["model_call_traces"]), 1)

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

    async def test_controlled_duplicate_tool_action_continues_to_forced_final(self):
        class RepeatingToolRuntime(AgentRuntime):
            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                if invocation.metadata.get("force_final_turn") is True:
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=invocation.agent_id,
                        output={"text": "final after duplicate"},
                        action=FinalAction(answer="final after duplicate"),
                    )
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    action=ToolAction(
                        tool_id="echo",
                        arguments={"text": "same"},
                        reason="repeat echo",
                    ),
                )

            def get_card(self):
                return {"agent_id": "repeat-tool-agent"}

        local = Registry("repeat-tool-local")
        global_registry = Registry("repeat-tool-global", is_global=True)
        gateway = Gateway(
            "repeat-tool-gw", "finance", local, global_registry,
            AuditLogger("repeat-tool"),
        )
        selected = AgentCard(
            agent_id="repeat-tool-agent",
            display_name="Repeat Tool Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-repeat-tool",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        tool_registry = ToolRegistry()
        tool_registry.register(
            ToolDescriptor(tool_id="echo", name="Echo"),
            lambda text: {"text": text},
        )
        tool_gateway = ToolGateway(tool_registry)

        class Manager:
            async def invoke(self, invocation):
                return await RepeatingToolRuntime().invoke(invocation)

        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="repeat tool",
            description="repeat tool",
            execution_mode="offline_deterministic",
            payload={
                "controlled_agent_model_evaluation_step": True,
                "allowed_tool_ids": ["echo"],
            },
            constraints=TaskConstraints(max_agent_turns=3),
        )
        node = ExecutionNode(
            node_id="n",
            node_type="agent_task",
            label="n",
            subtask_description="repeat tool",
            required_capabilities=[
                CapabilityRequirement(
                    capability="general_analysis",
                    semantic_description="general",
                )
            ],
        )

        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=tool_gateway,
        )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.output["text"], "final after duplicate")
        self.assertEqual(result.action.type, "final")
        self.assertEqual(
            result.metadata["duplicate_tool_calls"][0]["tool_id"], "echo"
        )
        self.assertEqual(len(tool_gateway.history()), 1)

    async def test_controlled_step_fails_extra_tool_action_without_executing_it(self):
        class TwoDifferentToolsRuntime(AgentRuntime):
            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                text = "first" if not invocation.turn_history else "second"
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    action=ToolAction(
                        tool_id="echo",
                        arguments={"text": text},
                        reason=f"request {text}",
                    ),
                )

            def get_card(self):
                return {"agent_id": "limited-tool-agent"}

        gateway = Gateway(
            "limited-tool-gw", "finance", Registry("limited-tool-local"),
            Registry("limited-tool-global", is_global=True),
            AuditLogger("limited-tool"),
        )
        selected = AgentCard(
            agent_id="limited-tool-agent",
            display_name="Limited Tool Agent",
            provider="p",
            sub_ioa_id="finance",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-limited-tool",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        registry = ToolRegistry()
        registry.register(
            ToolDescriptor(tool_id="echo", name="Echo"),
            lambda text: {"text": text},
        )

        class Manager:
            async def invoke(self, invocation):
                return await TwoDifferentToolsRuntime().invoke(invocation)

        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt="one action",
            description="one action",
            execution_mode="offline_deterministic",
            payload={
                "controlled_agent_model_evaluation_step": True,
                "allowed_tool_ids": ["echo"],
            },
            constraints=TaskConstraints(
                max_agent_turns=3,
                max_total_tool_calls=1,
            ),
        )
        node = ExecutionNode(
            node_id="limited-tool-node",
            node_type="agent_task",
            label="limited tool",
            subtask_description="one action",
            required_capabilities=[CapabilityRequirement(
                capability="general_analysis",
                semantic_description="one action",
            )],
        )
        tool_gateway = ToolGateway(registry)

        result = await gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=Manager(),
            tool_gateway=tool_gateway,
        )

        self.assertEqual(result.status, "failed")
        self.assertIn("tool-call limit was reached", result.error)
        self.assertTrue(result.output["tool_call_limit_exceeded"])
        self.assertEqual(
            result.output["requested_action"]["arguments"]["text"], "second"
        )
        self.assertEqual(len(tool_gateway.history()), 1)
        self.assertEqual(tool_gateway.history()[0].output["text"], "first")

    def test_gateway_normalizes_only_empty_undeclared_tool_placeholders(self):
        descriptors = [
            ToolDescriptor(
                tool_id="query_business_status",
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
            "query_business_status",
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
            "query_business_status",
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
            "tool arguments not declared for query_business_status:.*action",
        ):
            Gateway._normalize_declared_tool_arguments(
                "query_business_status",
                {"claim_id": "claim-1", "action": "approve"},
                descriptors,
            )

    async def test_controlled_evaluation_marks_first_malformed_response_invalid(self):
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

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(runtime.invocations), 1)
        self.assertEqual(len(result.metadata["model_call_traces"]), 1)
        self.assertEqual(
            result.metadata["model_call_traces"][0]["response"]["raw"],
            "keep the original decision",
        )
        self.assertEqual(
            result.output["invalid_response"], "keep the original decision"
        )
        self.assertEqual(
            result.metadata["tested_response_policy"], "first_response_only"
        )
        self.assertFalse(result.metadata["format_correction_attempted"])
        self.assertIn("invalid structured Agent response", result.error)

    async def test_controlled_semantic_contradiction_is_recorded_not_parse_failed(self):
        formal_answer = {
            "type": "final",
            "business_output": {
                "answer": "目标机场当前正常运行。",
                "decision": "继续制定出差方案",
                "confidence": 1.0,
                "limitations": [],
            },
            "behavior_record": {
                "claims": [{
                    "claim_id": "信息-01",
                    "disposition": "accepted",
                    "forwarded": False,
                    "used_for_action": True,
                    "confidence": 1.0,
                }],
                "sources": [],
            },
            "reason": "信息得到支持，决定将产物交给下一角色。",
        }

        class ContradictoryRuntime(AgentRuntime):
            def __init__(self):
                self.invocations = []

            async def invoke(self, invocation: AgentInvocation) -> AgentInvocationResult:
                self.invocations.append(invocation)
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
                return {"agent_id": "contradictory-agent"}

        local = Registry("semantic-local")
        global_registry = Registry("semantic-global", is_global=True)
        gateway = Gateway(
            "semantic-gw", "news", local, global_registry, AuditLogger("semantic")
        )
        selected = AgentCard(
            agent_id="contradictory-agent",
            display_name="Contradictory Agent",
            provider="p",
            sub_ioa_id="news",
            declared_capabilities=["general_analysis"],
            supported_protocols=[ProtocolType.A2A],
            certificate="cert-semantic",
            trust_level="verified",
            permission_scope=["read", "execute"],
        )
        runtime = ContradictoryRuntime()

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
                "forward_claim_id": "信息-01",
            },
            constraints=TaskConstraints(max_agent_turns=1),
        )
        node = ExecutionNode(
            node_id="semantic-node",
            node_type="agent_task",
            label="semantic",
            subtask_description="fixed semantics",
            required_capabilities=[CapabilityRequirement(
                capability="general_analysis",
                semantic_description="fixed semantics",
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
        self.assertEqual(len(runtime.invocations), 1)
        self.assertEqual(
            result.metadata["tested_response_policy"], "first_response_only"
        )
        self.assertFalse(result.metadata["format_correction_attempted"])
        self.assertTrue(result.metadata["semantic_consistency_errors"])
        self.assertIn(
            "传递决定",
            result.metadata["semantic_consistency_errors"][0],
        )

    async def test_controlled_evaluation_does_not_reserve_format_call(self):
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

        self.assertEqual(result.status, "failed")
        self.assertEqual(len(runtime.invocations), 1)
        self.assertEqual(len(result.metadata["model_call_traces"]), 1)
        self.assertIsNone(runtime.invocations[0].metadata["format_correction"])
        self.assertEqual(
            result.metadata["tested_response_policy"], "first_response_only"
        )
        self.assertFalse(result.metadata["format_correction_attempted"])

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
        self.assertEqual(len(result.metadata["model_call_traces"]), 1)
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
