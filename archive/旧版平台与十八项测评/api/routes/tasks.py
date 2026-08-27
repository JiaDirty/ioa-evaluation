"""Interactive IoA task API."""

from __future__ import annotations

import asyncio
import threading

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect

from api.schemas import HumanInputRequest, TaskCreateRequest, TaskFeedbackRequest, TaskResponse
from api.state import get_ioa_env, task_store
from src.core.data_models import Task, TaskStatus, TaskType
from src.orchestration import ExecutionGraph, ExecutionNode, OrchestrationPlan, StepStatus
from src.persistence.models import TaskRecord
from src.tasks.models import RuntimeTaskStatus

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.post("", response_model=TaskResponse)
async def create_task(req: TaskCreateRequest) -> TaskResponse:
    env = await get_ioa_env(req.execution_mode)
    scripted = _is_scripted_compat_request(req)
    if scripted:
        origin_sub_ioa = req.origin_sub_ioa or req.debug_overrides.origin_sub_ioa if req.debug_overrides else req.origin_sub_ioa
        origin_sub_ioa = origin_sub_ioa or "finance"
        if origin_sub_ioa not in env.get_sub_ioa_ids():
            raise HTTPException(status_code=404, detail=f"Unknown origin_sub_ioa: {origin_sub_ioa}")
        target_sub_ioas = req.target_sub_ioas or (
            req.debug_overrides.target_sub_ioas if req.debug_overrides else []
        ) or [origin_sub_ioa]
        task_type = TaskType.CROSS_DOMAIN if len(set(target_sub_ioas)) > 1 else TaskType.SINGLE_DOMAIN
        payload = dict(req.payload)
        if req.debug_overrides:
            payload.update(req.debug_overrides.payload)
        payload.setdefault("target_sub_ioa", origin_sub_ioa)
        payload.setdefault("target_sub_ioas", target_sub_ioas)
        if "required_capabilities_by_sub_ioa" not in payload:
            payload["required_capabilities_by_sub_ioa"] = {
                "finance": ["financial_analysis", "risk_assessment"],
                "healthcare": ["clinical_analysis"],
                "travel": ["flight_search"],
                "news": ["news_aggregation", "fact_checking"],
            }
        task = Task(
            task_type=task_type,
            prompt=req.prompt,
            description=req.prompt,
            user_goal=req.user_goal,
            origin_sub_ioa=origin_sub_ioa,
            target_sub_ioas=target_sub_ioas,
            required_capabilities=req.required_capabilities
            or (req.debug_overrides.required_capabilities if req.debug_overrides else []),
            constraints=req.constraints,
            execution_mode="scripted",
            payload=payload,
        )
    else:
        ignored_legacy_fields = {
            "origin_sub_ioa": req.origin_sub_ioa,
            "target_sub_ioas": req.target_sub_ioas,
            "required_capabilities": req.required_capabilities,
            "payload_keys": sorted(req.payload.keys()),
        }
        task = Task(
            task_type=TaskType.DYNAMIC,
            prompt=req.prompt,
            description=req.prompt,
            user_goal=req.user_goal,
            constraints=req.constraints,
            execution_mode=req.execution_mode,
            payload={
                "agentic_request": True,
                "ignored_legacy_route_fields": ignored_legacy_fields,
            },
        )
    env.task_store.create_task(
        TaskRecord(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            status=RuntimeTaskStatus.QUEUED.value if req.async_mode else TaskStatus.PENDING.value,
            description=task.description,
            payload={
                "request": req.model_dump(mode="json"),
                "task": task.model_dump(mode="json"),
            },
        )
    )
    if req.async_mode:
        await env.task_runner.submit(task)
        response = TaskResponse(
            task_id=task.task_id,
            trace_id=task.trace_id or task.task_id,
            status=RuntimeTaskStatus.QUEUED.value,
        )
        task_store[task.task_id] = {
            "request": req.model_dump(mode="json"),
            "task": task.model_dump(mode="json"),
            "response": response.model_dump(mode="json"),
            "feedback": [],
        }
        _schedule_background_run(env, task.task_id)
        return response

    result = await env.submit_task(task)
    response = _response_from_result(task.trace_id or task.task_id, result)
    env.task_store.update_task_status(
        task.task_id,
        response.status,
        result=response.model_dump(mode="json"),
        error=response.error,
    )
    for artifact in result.artifacts:
        env.artifact_store.append(artifact, trace_id=response.trace_id)
    task_store[task.task_id] = {
        "request": req.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "response": response.model_dump(mode="json"),
        "feedback": [],
    }
    return response


@router.get("")
async def list_tasks(limit: int = 50, offset: int = 0) -> list[dict]:
    env = await get_ioa_env()
    return [
        record.model_dump(mode="json")
        for record in env.task_store.list_tasks(limit=limit, offset=offset)
    ]


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str) -> TaskResponse:
    if task_id not in task_store:
        env = await get_ioa_env()
        record = env.task_store.get_task(task_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
        if record.result is not None:
            return TaskResponse(**record.result)
        return TaskResponse(
            task_id=record.task_id,
            trace_id=record.trace_id,
            status=record.status,
            error=record.error,
        )
    return TaskResponse(**task_store[task_id]["response"])


@router.get("/{task_id}/detail")
async def get_task_detail(task_id: str) -> dict:
    env = await get_ioa_env()
    stored = task_store.get(task_id)
    record = env.task_store.get_task(task_id)
    if stored is None and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    trace_id = (
        stored["task"].get("trace_id")
        if stored is not None
        else record.trace_id
    ) or task_id
    return {
        "task_id": task_id,
        "request": stored["request"] if stored is not None else record.payload.get("request", {}),
        "task": stored["task"] if stored is not None else record.payload.get("task", {}),
        "response": stored["response"] if stored is not None else record.result,
        "feedback": stored["feedback"] if stored is not None else [],
        "events": [event.model_dump(mode="json") for event in env.event_bus.query(trace_id=trace_id)],
        "tool_calls": env.tool_call_store.list_by_trace(trace_id),
        "artifacts": env.artifact_store.list_by_task(task_id),
    }


@router.get("/{task_id}/events")
async def get_task_events(task_id: str) -> list[dict]:
    env = await get_ioa_env()
    stored = task_store.get(task_id)
    record = env.task_store.get_task(task_id)
    if stored is None and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    trace_id = (stored["task"].get("trace_id") if stored is not None else record.trace_id) or task_id
    return [event.model_dump(mode="json") for event in env.event_bus.query(trace_id=trace_id)]


@router.get("/{task_id}/tool-calls")
async def get_task_tool_calls(task_id: str) -> list[dict]:
    env = await get_ioa_env()
    record = env.task_store.get_task(task_id)
    if task_id not in task_store and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    trace_id = record.trace_id if record is not None else task_store[task_id]["task"].get("trace_id", task_id)
    return env.tool_call_store.list_by_trace(trace_id)


@router.get("/{task_id}/artifacts")
async def get_task_artifacts(task_id: str) -> list[dict]:
    env = await get_ioa_env()
    if task_id not in task_store and env.task_store.get_task(task_id) is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return env.artifact_store.list_by_task(task_id)


@router.get("/{task_id}/execution-graph")
async def get_task_execution_graph(task_id: str) -> dict:
    env = await get_ioa_env()
    stored = task_store.get(task_id)
    record = env.task_store.get_task(task_id)
    if stored is None and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    task_payload = stored["task"] if stored is not None else record.payload.get("task", {})
    response = stored["response"] if stored is not None else record.result
    trace_id = (task_payload.get("trace_id") if isinstance(task_payload, dict) else "") or (
        record.trace_id if record is not None else task_id
    )
    events = env.event_bus.query(trace_id=trace_id)
    graph = _graph_from_events_or_task(task_id, trace_id, task_payload, response or {}, events)
    return graph.model_dump(mode="json")


@router.post("/{task_id}/cancel")
async def cancel_task(task_id: str) -> dict:
    env = await get_ioa_env()
    record = env.task_store.get_task(task_id)
    if task_id not in task_store and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    status = (record.status if record is not None else task_store[task_id]["response"]["status"])
    if status == RuntimeTaskStatus.COMPLETED.value or status == TaskStatus.COMPLETED.value:
        raise HTTPException(status_code=409, detail="completed task cannot be cancelled")
    if status == RuntimeTaskStatus.FAILED.value or status == TaskStatus.FAILED.value:
        raise HTTPException(status_code=409, detail="failed task should be retried, not cancelled")
    env.cancellation_registry.request_cancel(task_id)
    if status == RuntimeTaskStatus.QUEUED.value:
        env.task_store.update_task_status(task_id, RuntimeTaskStatus.CANCELLED.value, error="cancelled by user")
        _update_memory_response(task_id, RuntimeTaskStatus.CANCELLED.value, "cancelled by user")
    elif status == RuntimeTaskStatus.RUNNING.value:
        env.task_store.update_task_status(task_id, RuntimeTaskStatus.CANCEL_REQUESTED.value)
        _update_memory_response(task_id, RuntimeTaskStatus.CANCEL_REQUESTED.value, None)
    else:
        env.task_store.update_task_status(task_id, RuntimeTaskStatus.CANCELLED.value, error="cancelled by user")
        _update_memory_response(task_id, RuntimeTaskStatus.CANCELLED.value, "cancelled by user")
    return {"task_id": task_id, "status": "cancelled"}


@router.post("/{task_id}/retry", response_model=TaskResponse)
async def retry_task(task_id: str) -> TaskResponse:
    env = await get_ioa_env()
    record = env.task_store.get_task(task_id)
    if task_id not in task_store and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    status = record.status if record is not None else task_store[task_id]["response"]["status"]
    if status not in {RuntimeTaskStatus.FAILED.value, RuntimeTaskStatus.CANCELLED.value, TaskStatus.FAILED.value}:
        raise HTTPException(status_code=409, detail=f"task status does not support retry: {status}")
    request_payload = task_store[task_id]["request"] if task_id in task_store else record.payload.get("request", {})
    request_payload = {**request_payload, "async_mode": True}
    return await create_task(TaskCreateRequest(**request_payload))


@router.post("/{task_id}/feedback")
async def task_feedback(task_id: str, feedback: TaskFeedbackRequest) -> dict:
    if task_id not in task_store:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    task_store[task_id]["feedback"].append(feedback.model_dump(mode="json"))
    return {"task_id": task_id, "feedback_count": len(task_store[task_id]["feedback"])}


@router.post("/{task_id}/human-input")
async def task_human_input(task_id: str, human_input: HumanInputRequest) -> dict:
    env = await get_ioa_env()
    stored = task_store.get(task_id)
    record = env.task_store.get_task(task_id)
    if stored is None and record is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    trace_id = (
        stored["task"].get("trace_id")
        if stored is not None
        else record.trace_id
    ) or task_id
    payload = human_input.model_dump(mode="json")
    if stored is not None:
        stored.setdefault("human_inputs", []).append(payload)
    env.event_bus.emit(
        task_id=task_id,
        trace_id=trace_id,
        stage="waiting_human_input",
        event_type="human_input_received",
        actor_type="user",
        actor_id="user",
        message="Human input received for checkpoint",
        status="approved" if human_input.approved else "rejected",
        payload=payload,
    )
    if not human_input.approved:
        env.task_store.update_task_status(task_id, TaskStatus.CANCELLED.value, error="human checkpoint rejected")
        _update_memory_response(task_id, TaskStatus.CANCELLED.value, "human checkpoint rejected")
        env.event_bus.emit(
            task_id=task_id,
            trace_id=trace_id,
            stage="cancelled",
            event_type="human_checkpoint_rejected",
            actor_type="orchestrator",
            actor_id="AgenticOrchestrator",
            message="Task cancelled after human checkpoint rejection",
            status="cancelled",
            payload={"checkpoint_id": human_input.checkpoint_id},
        )
        return {
            "task_id": task_id,
            "checkpoint_id": human_input.checkpoint_id,
            "recorded": True,
            "resumed": False,
            "status": TaskStatus.CANCELLED.value,
        }

    task_payload = stored["task"] if stored is not None else record.payload.get("task", {})
    task = Task.model_validate(task_payload)
    task.status = TaskStatus.PENDING
    previous_simulation = env.agentic_orchestrator.simulate_human_checkpoints
    env.agentic_orchestrator.simulate_human_checkpoints = True
    try:
        result = await env.submit_task(task)
    finally:
        env.agentic_orchestrator.simulate_human_checkpoints = previous_simulation
    response = _response_from_result(trace_id, result)
    env.task_store.update_task_status(
        task_id,
        response.status,
        result=response.model_dump(mode="json"),
        error=response.error,
    )
    if stored is not None:
        stored["response"] = response.model_dump(mode="json")
    env.event_bus.emit(
        task_id=task_id,
        trace_id=trace_id,
        stage="running",
        event_type="human_checkpoint_resumed",
        actor_type="orchestrator",
        actor_id="AgenticOrchestrator",
        message="Task resumed after human checkpoint approval",
        status=response.status,
        payload={"checkpoint_id": human_input.checkpoint_id, "result_status": response.status},
    )
    return {
        "task_id": task_id,
        "checkpoint_id": human_input.checkpoint_id,
        "recorded": True,
        "resumed": True,
        "status": response.status,
    }


def _response_from_result(trace_id: str, result) -> TaskResponse:
    return TaskResponse(
        task_id=result.task_id,
        trace_id=trace_id,
        status=result.status.value if hasattr(result.status, "value") else str(result.status),
        output=result.output,
        artifacts=[artifact.model_dump(mode="json") for artifact in result.artifacts],
        participating_agents=result.participating_agents,
        error=result.error,
    )


def _is_scripted_compat_request(req: TaskCreateRequest) -> bool:
    if req.execution_mode == "scripted":
        return True
    return bool(
        req.compat_description_alias_used
        and (
            req.origin_sub_ioa is not None
            or req.target_sub_ioas
            or req.required_capabilities
            or req.payload
        )
    )


def _graph_from_events_or_task(
    task_id: str,
    trace_id: str,
    task_payload: dict,
    response: dict,
    events: list,
) -> ExecutionGraph:
    plan_payload = None
    agentic_graph_payload = None
    for event in events:
        if event.event_type == "agentic_plan_created" and isinstance(event.payload, dict):
            graph_payload = event.payload.get("execution_graph")
            if graph_payload:
                agentic_graph_payload = graph_payload
        if event.event_type in {"agentic_task_completed", "graph_replanned"} and isinstance(event.payload, dict):
            graph_payload = event.payload.get("execution_graph") or event.payload.get("after_graph")
            if graph_payload:
                agentic_graph_payload = graph_payload
        if event.event_type == "orchestration_planned" and isinstance(event.payload, dict):
            plan_payload = event.payload
            break
        if event.payload.get("plan"):
            plan_payload = event.payload["plan"]
    if agentic_graph_payload:
        graph = ExecutionGraph(**agentic_graph_payload)
    elif plan_payload:
        graph = OrchestrationPlan(**plan_payload).to_execution_graph(trace_id=trace_id)
    else:
        participants = response.get("participating_agents") or []
        nodes = [
            ExecutionNode(
                node_id="task_intake",
                node_type="verify",
                label="Task intake and policy checks",
                status=StepStatus.COMPLETED if response else StepStatus.PENDING,
                input={"description": task_payload.get("description", "")},
            )
        ]
        for agent_id in participants:
            nodes.append(
                ExecutionNode(
                    node_id=f"agent-{agent_id}",
                    node_type="agent",
                    label=f"Agent {agent_id}",
                    target_id=agent_id,
                    depends_on=["task_intake"],
                    status=StepStatus.COMPLETED if response.get("status") == "completed" else StepStatus.PENDING,
                )
            )
        aggregate_dependencies = [node.node_id for node in nodes if node.node_type == "agent"] or ["task_intake"]
        nodes.append(
            ExecutionNode(
                node_id="aggregate",
                node_type="aggregate",
                label="Aggregate result",
                depends_on=aggregate_dependencies,
                status=StepStatus.COMPLETED if response.get("status") == "completed" else StepStatus.PENDING,
                output={"artifact_count": len(response.get("artifacts") or [])},
            )
        )
        graph = ExecutionGraph(task_id=task_id, trace_id=trace_id, nodes=nodes)
        graph.refresh_edges()

    completed_agents = {
        event.actor_id
        for event in events
        if event.event_type in {"agent_step_completed", "delivery_completed"} and event.actor_id
    }
    for node in graph.nodes:
        if node.target_id in completed_agents:
            node.status = StepStatus.COMPLETED
        if response.get("status") == "failed" and node.status == StepStatus.PENDING:
            node.status = StepStatus.SKIPPED
    if response.get("status") == "completed":
        for node in graph.nodes:
            if node.status == StepStatus.PENDING:
                node.status = StepStatus.COMPLETED
    return graph


@router.websocket("/{task_id}/stream")
async def stream_task_events(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()
    after_sequence = int(websocket.query_params.get("after_sequence", "0") or 0)
    try:
        env = await get_ioa_env()
        record = env.task_store.get_task(task_id)
        if record is None and task_id not in task_store:
            await websocket.send_json({"type": "error", "message": f"Task not found: {task_id}"})
            await websocket.close()
            return
        while True:
            events = env.event_bus.query(task_id=task_id, after_sequence=after_sequence)
            for event in events:
                after_sequence = max(after_sequence, event.sequence)
                await websocket.send_json({"type": "event", "event": event.model_dump(mode="json")})
            record = env.task_store.get_task(task_id)
            status = record.status if record is not None else task_store[task_id]["response"]["status"]
            await websocket.send_json({
                "type": "status", "task_id": task_id, "status": status,
                "last_sequence": after_sequence,
            })
            if status in {"completed", "failed", "cancelled"}:
                await websocket.send_json({"type": "complete", "task_id": task_id, "status": status})
                break
            await asyncio.sleep(0.25)
    except WebSocketDisconnect:
        return


async def _run_task_later(env, task_id: str) -> None:
    await asyncio.sleep(0.05)
    await env.task_runner.run_once(task_id)
    record = env.task_store.get_task(task_id)
    if record is not None and record.result is not None and task_id in task_store:
        task_store[task_id]["response"] = record.result
    elif record is not None and task_id in task_store:
        _update_memory_response(task_id, record.status, record.error)


def _update_memory_response(task_id: str, status: str, error: str | None) -> None:
    if task_id not in task_store:
        return
    task_store[task_id]["response"]["status"] = status
    task_store[task_id]["response"]["error"] = error


def _schedule_background_run(env, task_id: str) -> None:
    def runner() -> None:
        asyncio.run(_run_task_later(env, task_id))

    thread = threading.Thread(target=runner, name=f"ioa-task-runner-{task_id}", daemon=True)
    thread.start()
