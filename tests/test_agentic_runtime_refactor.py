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
                if invocation.turn_history:
                    return AgentInvocationResult(
                        task_id=invocation.task_id,
                        trace_id=invocation.trace_id,
                        agent_id=invocation.agent_id,
                        output={"text": "final after tool"},
                        action=FinalAction(answer="final after tool", evidence_artifact_ids=[]),
                    )
                return AgentInvocationResult(
                    task_id=invocation.task_id,
                    trace_id=invocation.trace_id,
                    agent_id=invocation.agent_id,
                    action=ToolAction(tool_id="echo", arguments={"text": "hello"}, reason="need echo"),
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
        task = Task(task_type=TaskType.DYNAMIC, prompt="use tool", description="use tool")
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
