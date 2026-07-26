"""Default prompt-driven agentic task control plane."""

from __future__ import annotations

from enum import Enum
from typing import Any

from ..core.data_models import Artifact, Task, TaskResult, TaskStatus
from ..decision_agents.replanning import ReplanningAgent
from ..decision_agents.synthesis import SynthesisAgent
from ..decision_agents.task_specification import TaskSpecificationAgent
from ..runtime.actions import DelegationAction
from .delegation import DelegationController, DelegationGrant, DelegationRequest
from .graph import ExecutionGraph, ExecutionNode, StepStatus
from .plan_validator import PlanValidator
from .planner import AgenticOrchestrationPlanner


class AgenticTaskState(str, Enum):
    RECEIVED = "received"
    SPECIFYING = "specifying"
    PLANNING = "planning"
    EXECUTING = "executing"
    WAITING_HUMAN_INPUT = "waiting_human_input"
    REPLANNING = "replanning"
    SYNTHESIZING = "synthesizing"
    SECURITY_REVIEW = "security_review"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgenticOrchestrator:
    def __init__(
        self,
        *,
        gateways: dict[str, Any],
        global_registry: Any,
        runtime_manager: Any,
        tool_gateway: Any,
        event_bus: Any | None = None,
        task_spec_agent: TaskSpecificationAgent | None = None,
        planner: AgenticOrchestrationPlanner | None = None,
        validator: PlanValidator | None = None,
        synthesis_agent: SynthesisAgent | None = None,
        replanning_agent: ReplanningAgent | None = None,
        delegation_controller: DelegationController | None = None,
        simulate_human_checkpoints: bool = True,
    ) -> None:
        self.gateways = gateways
        self.global_registry = global_registry
        self.runtime_manager = runtime_manager
        self.tool_gateway = tool_gateway
        self.event_bus = event_bus
        self.task_spec_agent = task_spec_agent or TaskSpecificationAgent()
        self.planner = planner or AgenticOrchestrationPlanner()
        self.validator = validator or PlanValidator()
        self.synthesis_agent = synthesis_agent or SynthesisAgent()
        self.replanning_agent = replanning_agent or ReplanningAgent()
        self.delegation_controller = delegation_controller or DelegationController()
        self.simulate_human_checkpoints = simulate_human_checkpoints

    async def execute(self, task: Task) -> TaskResult:
        if not task.trace_id:
            task.trace_id = task.task_id
        task.status = TaskStatus.IN_PROGRESS
        self._emit(task, AgenticTaskState.RECEIVED, "task_received", "Agentic task received")

        try:
            if task.task_spec is None:
                self._emit(task, AgenticTaskState.SPECIFYING, "task_specification_started", "Building TaskSpec")
                registered_agents = await self.global_registry.list_agents()
                available_capabilities = sorted({
                    capability
                    for agent in registered_agents
                    if not agent.agent_id.endswith("-gw")
                    and agent.trust_level in {"verified", "privileged"}
                    for capability in agent.declared_capabilities
                })
                task.task_spec = self.task_spec_agent.specify(
                    prompt=task.prompt or task.description,
                    constraints=task.constraints,
                    user_goal=task.user_goal,
                    available_capabilities=available_capabilities,
                )
            else:
                controlled_step = (
                    task.payload.get("controlled_agent_model_evaluation_step") is True
                )
                self._emit(
                    task,
                    AgenticTaskState.SPECIFYING,
                    (
                        "controlled_task_specification_used"
                        if controlled_step else "task_specification_reused"
                    ),
                    (
                        "Using the pre-registered fixed TaskSpec for this evaluation step"
                        if controlled_step else
                        "Reusing the existing TaskSpec"
                    ),
                )

            self._emit(
                task,
                AgenticTaskState.PLANNING,
                "task_spec_created",
                "TaskSpec ready for planning",
                payload={"task_spec": task.task_spec.model_dump(mode="json")},
            )
            graph = self.planner.build_graph(task, task.task_spec)
            task.active_plan_id = graph.graph_id
            self.validator.assert_valid(
                graph,
                max_nodes=task.constraints.max_plan_nodes,
                max_depth=task.constraints.max_delegation_depth + task.constraints.max_plan_nodes,
            )
            self._emit(
                task,
                AgenticTaskState.PLANNING,
                "agentic_plan_created",
                "Capability-level plan created without bound Agent IDs",
                payload={"execution_graph": graph.model_dump(mode="json")},
            )

            artifacts = await self._execute_graph(task, graph)
            if task.status == TaskStatus.WAITING_HUMAN_INPUT:
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.WAITING_HUMAN_INPUT,
                    output={
                        "state": AgenticTaskState.WAITING_HUMAN_INPUT.value,
                        "active_plan_id": graph.graph_id,
                    },
                    artifacts=artifacts,
                    participating_agents=self._participating_agents(graph),
                    metadata={"model_call_traces": _collect_model_call_traces(graph)},
                )
            if any(node.status == StepStatus.FAILED for node in graph.nodes if node.node_type == "agent_task"):
                error = "; ".join(
                    node.error or f"{node.node_id} failed"
                    for node in graph.nodes
                    if node.status == StepStatus.FAILED
                )
                task.status = TaskStatus.FAILED
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.FAILED,
                    error=error,
                    artifacts=artifacts,
                    participating_agents=self._participating_agents(graph),
                    metadata={"model_call_traces": _collect_model_call_traces(graph)},
                )

            if task.payload.get("controlled_agent_model_evaluation_step") is True:
                primary_artifact = next(
                    (
                        artifact for artifact in reversed(artifacts)
                        if artifact.producer_agent_id != "SynthesisAgent"
                    ),
                    None,
                )
                if primary_artifact is None:
                    raise RuntimeError(
                        "Controlled evaluation step completed without a tested-Agent artifact"
                    )
                self._emit(
                    task,
                    AgenticTaskState.COMPLETED,
                    "controlled_evaluation_step_completed",
                    "Controlled evaluation step completed without auxiliary synthesis",
                    payload={"primary_artifact_id": primary_artifact.artifact_id},
                )
                task.status = TaskStatus.COMPLETED
                return TaskResult(
                    task_id=task.task_id,
                    status=TaskStatus.COMPLETED,
                    output=primary_artifact.content,
                    artifacts=artifacts,
                    participating_agents=self._participating_agents(graph),
                    metadata={
                        "model_call_traces": _collect_model_call_traces(graph),
                        "controlled_evaluation_step": True,
                        "auxiliary_synthesis_skipped": True,
                    },
                )

            self._emit(task, AgenticTaskState.SYNTHESIZING, "synthesis_started", "Synthesizing artifacts")
            decision = self.synthesis_agent.synthesize(
                task_spec=task.task_spec,
                artifacts=artifacts,
            )
            final_artifact = Artifact(
                task_id=task.task_id,
                producer_agent_id="SynthesisAgent",
                protocol="agentic_synthesis",
                artifact_type="structured_report",
                content=decision.model_dump(mode="json"),
                content_type="application/json",
                source_agent_id="SynthesisAgent",
                source_task_id=task.task_id,
                safe=True,
                agent_contributions=[
                    {
                        "agent_id": node.assigned_agent_id,
                        "role": "selected_agent",
                        "artifact_id": node.output.get("artifact_id"),
                        "summary": str(node.output.get("text", ""))[:160],
                    }
                    for node in graph.nodes
                    if node.node_type == "agent_task" and node.assigned_agent_id
                ],
                metadata={
                    "trace_id": task.trace_id,
                    "task_spec": task.task_spec.model_dump(mode="json"),
                    "execution_graph": graph.model_dump(mode="json"),
                    "evidence_map": decision.evidence_map,
                    "plan_revisions": task.plan_revisions,
                },
            )
            artifacts.append(final_artifact)
            self._emit(
                task,
                AgenticTaskState.COMPLETED,
                "agentic_task_completed",
                "Agentic task completed",
                payload={
                    "final_artifact_id": final_artifact.artifact_id,
                    "participating_agents": self._participating_agents(graph),
                    "execution_graph": graph.model_dump(mode="json"),
                },
            )
            task.status = TaskStatus.COMPLETED
            return TaskResult(
                task_id=task.task_id,
                status=TaskStatus.COMPLETED,
                output=decision.model_dump(mode="json"),
                artifacts=artifacts,
                participating_agents=self._participating_agents(graph),
                metadata={"model_call_traces": _collect_model_call_traces(graph)},
            )
        except Exception as exc:
            task.status = TaskStatus.FAILED
            self._emit(
                task,
                AgenticTaskState.FAILED,
                "agentic_task_failed",
                str(exc),
                status="failed",
            )
            return TaskResult(task_id=task.task_id, status=TaskStatus.FAILED, error=str(exc))

    async def _execute_graph(self, task: Task, graph: ExecutionGraph) -> list[Artifact]:
        artifacts: list[Artifact] = []
        completed: set[str] = set()

        while True:
            ready = [
                node
                for node in graph.nodes
                if node.status == StepStatus.PENDING
                and all(dependency in completed for dependency in node.depends_on)
            ]
            if not ready:
                break
            for node in ready:
                node.status = StepStatus.RUNNING
                node_span = self._start_node_span(task, graph, node)
                if node.node_type in {"policy_check", "verify"}:
                    node.status = StepStatus.COMPLETED
                    node.output = {"policy": "precheck_complete"}
                    completed.add(node.node_id)
                elif node.node_type == "agent_task":
                    artifact = await self._run_agent_node(task, graph, node)
                    if artifact is not None:
                        artifacts.append(artifact)
                    if node.status in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
                        completed.add(node.node_id)
                elif node.node_type == "human":
                    if not self.simulate_human_checkpoints:
                        node.status = StepStatus.PENDING
                        node.output = {
                            "checkpoint": node.metadata,
                            "human_input_required_before_side_effect": True,
                            "simulated": False,
                            "state": AgenticTaskState.WAITING_HUMAN_INPUT.value,
                        }
                        task.status = TaskStatus.WAITING_HUMAN_INPUT
                        self._emit(
                            task,
                            AgenticTaskState.WAITING_HUMAN_INPUT,
                            "human_checkpoint_waiting",
                            "Human checkpoint is waiting for explicit input",
                            actor_type="policy_engine",
                            payload={"node_id": node.node_id, "checkpoint": node.metadata},
                        )
                        self._finish_node_span(task, graph, node, node_span, phase="waiting")
                        return artifacts
                    node.status = StepStatus.COMPLETED
                    node.output = {
                        "checkpoint": node.metadata,
                        "human_input_required_before_side_effect": True,
                        "simulated": True,
                        "simulation_mode": "offline_human_simulator",
                    }
                    self._emit(
                        task,
                        AgenticTaskState.EXECUTING,
                        "human_checkpoint_registered",
                        "Human checkpoint preserved in execution graph",
                        actor_type="policy_engine",
                        payload={"node_id": node.node_id, "checkpoint": node.metadata},
                    )
                    completed.add(node.node_id)
                elif node.node_type == "synthesis":
                    node.status = StepStatus.COMPLETED
                    node.output = {"artifact_count": len(artifacts)}
                    completed.add(node.node_id)
                else:
                    node.status = StepStatus.SKIPPED
                    completed.add(node.node_id)
                self._finish_node_span(task, graph, node, node_span)
        return artifacts

    async def _run_agent_node(
        self, task: Task, graph: ExecutionGraph, node: ExecutionNode
    ) -> Artifact | None:
        requirement = node.required_capabilities[0]
        entry_gateway = self._select_entry_gateway(requirement, task)
        self._emit(
            task,
            AgenticTaskState.EXECUTING,
            "entry_gateway_selected",
            "Entry Gateway selected from capability/domain context",
            payload={
                "entry_sub_ioa_id": entry_gateway.sub_ioa_id,
                "capability": requirement.capability,
                "selection_basis": "capability_domain_map_or_registration_order",
            },
        )
        selected = await entry_gateway.discover_and_select(requirement, task=task)
        node.assigned_agent_id = selected.agent_id
        node.assigned_sub_ioa_id = selected.sub_ioa_id
        node.metadata["selected_at_runtime"] = True
        if selected.sub_ioa_id != entry_gateway.sub_ioa_id:
            self._emit(
                task,
                AgenticTaskState.EXECUTING,
                "dynamic_cross_domain_relay",
                "Selected Agent belongs to a different Sub-IoA than the entry Gateway",
                payload={
                    "entry_sub_ioa_id": entry_gateway.sub_ioa_id,
                    "selected_sub_ioa_id": selected.sub_ioa_id,
                    "selected_agent_id": selected.agent_id,
                },
            )

        result = await entry_gateway.dispatch_agentic_subtask(
            task=task,
            node=node,
            selected_agent=selected,
            runtime_manager=self.runtime_manager,
            tool_gateway=self.tool_gateway,
            delegation_grant=node.metadata.get("delegation_grant") or None,
        )
        node.metadata["runtime_result_metadata"] = result.metadata
        action = result.action
        if action is not None and action.type == "delegate":
            await self._append_delegation_node(task, graph, node, action)
            node.status = StepStatus.COMPLETED
            node.output = {"delegation_requested": action.model_dump(mode="json")}
            return None
        if action is not None and action.type == "replan":
            if len(task.plan_revisions) >= task.constraints.max_plan_revisions:
                node.status = StepStatus.FAILED
                node.error = "replan revision limit exceeded"
                return None
            before = graph.model_dump(mode="json")
            if not node.required_capabilities:
                node.status = StepStatus.FAILED
                node.error = "replan action did not preserve a capability requirement"
                return None
            self.replanning_agent.add_unfinished_capability(
                graph,
                reason=action.reason,
                requirement=node.required_capabilities[0],
                depends_on=list(node.depends_on),
            )
            node.status = StepStatus.SKIPPED
            task.plan_revisions.append({
                "type": "replan",
                "reason": action.reason,
                "before_graph": before,
                "after_graph": graph.model_dump(mode="json"),
            })
            self._emit(
                task,
                AgenticTaskState.REPLANNING,
                "graph_replanned",
                action.reason,
                payload={"before_graph": before, "after_graph": graph.model_dump(mode="json")},
            )
            return None
        if action is not None and action.type == "fail" and action.recoverable:
            if len(task.plan_revisions) >= task.constraints.max_plan_revisions:
                node.status = StepStatus.FAILED
                node.error = "recoverable failure exceeded replan limit"
                return None
            before = graph.model_dump(mode="json")
            if not node.required_capabilities:
                node.status = StepStatus.FAILED
                node.error = "recoverable failure did not preserve a capability requirement"
                return None
            self.replanning_agent.add_unfinished_capability(
                graph,
                reason=action.message,
                requirement=node.required_capabilities[0],
                depends_on=list(node.depends_on),
            )
            node.status = StepStatus.SKIPPED
            task.plan_revisions.append({
                "type": "recoverable_failure_replan",
                "reason": action.message,
                "before_graph": before,
                "after_graph": graph.model_dump(mode="json"),
            })
            return None
        if result.status == "input_required" or (action is not None and action.type == "ask_user"):
            node.status = StepStatus.FAILED
            node.error = "human input required to continue"
            task.status = TaskStatus.WAITING_HUMAN_INPUT
            return None
        if result.status != "completed":
            node.status = StepStatus.FAILED
            node.error = result.error or "agent failed"
            return None

        text = result.output.get("text", result.output)
        artifact = Artifact(
            task_id=task.task_id,
            producer_agent_id=selected.agent_id,
            protocol=str(result.metadata.get("protocol", "agentic_runtime")),
            artifact_type="text_answer",
            content={"text": text, "node_id": node.node_id},
            content_type="application/json",
            source_agent_id=selected.agent_id,
            source_task_id=task.task_id,
            safe=True,
            agent_contributions=[{
                "agent_id": selected.agent_id,
                "role": "runtime_selected_agent",
                "summary": str(text)[:160],
            }],
            tool_call_refs=[call.get("call_id", "") for call in result.tool_calls],
            metadata={
                "trace_id": task.trace_id,
                "node_id": node.node_id,
                "requirement": requirement.model_dump(mode="json"),
                "runtime_action": action.model_dump(mode="json") if action else None,
                "tool_calls": result.tool_calls,
                "selected_agent_id": selected.agent_id,
                "selected_sub_ioa_id": selected.sub_ioa_id,
                "model_call_traces": result.metadata.get("model_call_traces", []),
            },
        )
        node.status = StepStatus.COMPLETED
        node.output = {"artifact_id": artifact.artifact_id, "text": str(text)}
        self._emit(
            task,
            AgenticTaskState.EXECUTING,
            "agent_node_completed",
            "Agent capability node completed",
            actor_type="domain_agent",
            actor_id=selected.agent_id,
            payload={
                "node_id": node.node_id,
                "artifact_id": artifact.artifact_id,
                "capability": requirement.capability,
            },
        )
        return artifact

    async def _append_delegation_node(
        self,
        task: Task,
        graph: ExecutionGraph,
        parent_node: ExecutionNode,
        action: DelegationAction,
    ) -> None:
        request = DelegationRequest(
            parent_task_id=task.task_id,
            parent_node_id=parent_node.node_id,
            requester_agent_id=parent_node.assigned_agent_id or "",
            objective=action.objective,
            required_capabilities=action.required_capabilities,
            requested_scopes=action.requested_scopes,
            input_artifact_ids=action.input_artifact_ids,
            expected_output=action.expected_output,
            reason=action.reason,
        )
        parent_grant = self._parent_grant_from_node(parent_node)
        decision = self.delegation_controller.evaluate_request(
            request,
            parent_grant=parent_grant,
            user_scopes=task.user_grants or ["read", "execute", "delegate"],
            policy_scopes=["read", "execute", "delegate"],
            max_depth=task.constraints.max_delegation_depth,
        )
        self._emit(
            task,
            AgenticTaskState.EXECUTING,
            "delegation_request_evaluated",
            decision.reason,
            actor_type="policy_engine",
            payload={
                "request": request.model_dump(mode="json"),
                "decision": decision.model_dump(mode="json"),
            },
        )
        if not decision.allowed:
            parent_node.status = StepStatus.FAILED
            parent_node.error = decision.reason
            return
        node = ExecutionNode(
            node_id=f"delegated-{len(graph.nodes) + 1}",
            node_type="agent_task",
            label=f"Delegated capability: {action.objective}",
            depends_on=[parent_node.node_id],
            subtask_description=action.objective,
            required_capabilities=action.required_capabilities,
            expected_output=action.expected_output,
            metadata={
                "delegation_grant": decision.grant.model_dump(mode="json") if decision.grant else {},
                "dynamic_delegation": True,
            },
        )
        graph.nodes.append(node)
        graph.refresh_edges()
        task.plan_revisions.append({
            "reason": action.reason,
            "added_node_id": node.node_id,
            "type": "delegation",
        })

    def _parent_grant_from_node(self, node: ExecutionNode) -> DelegationGrant | None:
        raw = node.metadata.get("delegation_grant")
        if not raw:
            return None
        try:
            return DelegationGrant.model_validate(raw)
        except Exception:
            return None

    def _select_entry_gateway(self, requirement=None, task: Task | None = None):
        if not self.gateways:
            raise ValueError("No Gateway registered for agentic task execution")
        evaluation_sub_ioa = (
            str(task.payload.get("evaluation_preferred_agent_sub_ioa", ""))
            if task is not None else ""
        )
        if evaluation_sub_ioa:
            gateway = self.gateways.get(evaluation_sub_ioa)
            if gateway is None:
                raise ValueError(
                    "Paired evaluation Agent gateway is unavailable: "
                    f"{evaluation_sub_ioa}"
                )
            return gateway
        capability = getattr(requirement, "capability", "")
        preferred_domains = list(getattr(requirement, "preferred_domains", []) or [])
        for domain in preferred_domains:
            if domain in self.gateways:
                return self.gateways[domain]
        capability_domains = {
            "financial": "finance",
            "investment": "finance",
            "risk": "finance",
            "clinical": "healthcare",
            "health": "healthcare",
            "public_health": "healthcare",
            "medical": "healthcare",
            "itinerary": "travel",
            "travel": "travel",
            "insurance": "travel",
            "logistics": "travel",
            "news": "news",
            "sentiment": "news",
            "fact": "news",
        }
        for token, sub_ioa_id in capability_domains.items():
            if token in capability and sub_ioa_id in self.gateways:
                return self.gateways[sub_ioa_id]
        return next(iter(self.gateways.values()))

    def _participating_agents(self, graph: ExecutionGraph) -> list[str]:
        agents = [
            node.assigned_agent_id
            for node in graph.nodes
            if node.assigned_agent_id
        ]
        return sorted(set(agents))

    def _emit(
        self,
        task: Task,
        state: AgenticTaskState,
        event_type: str,
        message: str,
        *,
        status: str = "ok",
        actor_type: str = "orchestrator",
        actor_id: str = "AgenticOrchestrator",
        payload: dict[str, Any] | None = None,
    ) -> None:
        if self.event_bus is None:
            return
        self.event_bus.emit(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            stage=state.value,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            message=message,
            status=status,
            payload=payload or {},
        )

    def _start_node_span(self, task: Task, graph: ExecutionGraph, node: ExecutionNode):
        if self.event_bus is None:
            return None
        parent_span_id = None
        for dependency_id in reversed(node.depends_on):
            dependency = graph.node_by_id(dependency_id)
            if dependency and dependency.metadata.get("observability_span_id"):
                parent_span_id = dependency.metadata["observability_span_id"]
                break
        event = self.event_bus.start_span(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            stage=AgenticTaskState.EXECUTING.value,
            event_type="execution_node_started",
            actor_type=node.node_type,
            actor_id=node.assigned_agent_id or node.target_id or node.node_id,
            message=f"Started: {node.label}",
            parent_span_id=parent_span_id,
            graph_id=graph.graph_id,
            node_id=node.node_id,
            operation=f"execution_node.{node.node_type}",
            input={
                "subtask": node.subtask_description,
                "input": node.input,
                "input_bindings": [item.model_dump(mode="json") for item in node.input_bindings],
                "required_capabilities": [item.model_dump(mode="json") for item in node.required_capabilities],
                "expected_output": node.expected_output,
            },
            upstream_ids=list(node.depends_on),
            downstream_ids=[node.node_id],
        )
        node.metadata["observability_span_id"] = event.span_id
        return event

    def _finish_node_span(self, task: Task, graph: ExecutionGraph, node: ExecutionNode,
                          event, *, phase: str | None = None) -> None:
        if self.event_bus is None or event is None:
            return
        resolved_phase = phase or {
            StepStatus.COMPLETED: "completed",
            StepStatus.FAILED: "failed",
            StepStatus.SKIPPED: "skipped",
            StepStatus.CANCELLED: "cancelled",
            StepStatus.RUNNING: "started",
        }.get(node.status, "completed")
        self.event_bus.finish_span(
            span_id=event.span_id,
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            stage=AgenticTaskState.EXECUTING.value,
            event_type=f"execution_node_{resolved_phase}",
            actor_type=node.node_type,
            actor_id=node.assigned_agent_id or node.target_id or node.node_id,
            message=node.error or f"{resolved_phase.title()}: {node.label}",
            graph_id=graph.graph_id,
            node_id=node.node_id,
            operation=f"execution_node.{node.node_type}",
            phase=resolved_phase,
            status=resolved_phase,
            output=node.output,
            upstream_ids=list(node.depends_on),
            downstream_ids=[node.node_id],
            error=node.error,
        )


def _collect_model_call_traces(graph: ExecutionGraph) -> list[dict[str, Any]]:
    traces: list[dict[str, Any]] = []
    for node in graph.nodes:
        metadata = node.metadata.get("runtime_result_metadata", {})
        if not isinstance(metadata, dict):
            continue
        node_traces = metadata.get("model_call_traces", [])
        if isinstance(node_traces, list):
            traces.extend(
                {"node_id": node.node_id, **trace}
                for trace in node_traces
                if isinstance(trace, dict)
            )
    return traces
