# IoA Runtime Upgrade

## Current Default: Prompt-Only Agentic Runtime

The default runtime path is now `execution_mode="agentic"`. A normal caller only
submits `prompt` plus optional `constraints`; `origin_sub_ioa`,
`target_sub_ioas`, `required_capabilities`, `task_type`, concrete Agent IDs, and
`hop_chain` are not execution inputs for this path.

```json
{
  "prompt": "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较旅行保险；任何购买必须先确认。",
  "constraints": {
    "max_budget": 30000,
    "max_plan_nodes": 12,
    "max_delegation_depth": 4,
    "human_approval_for_side_effects": true,
    "require_citations": true,
    "allow_cross_domain_relay": true
  }
}
```

The runtime flow is:

```text
prompt -> TaskSpecificationAgent -> AgenticOrchestrationPlanner
       -> PlanValidator -> Gateway discovery/selection/protocol/runtime loop
       -> SynthesisAgent -> TaskResult + ExecutionGraph + EvidenceBundle
```

The old fixed-route behavior remains available only as `execution_mode="scripted"`.

The unified event, span, replay, WebSocket, and runtime-console design is
documented in [runtime_observability.md](runtime_observability.md).

## Architecture

```text
User / Frontend
  -> Task API
  -> Gateway
  -> Policy + Decision Agents
  -> Registry Discovery
  -> Orchestration Plan
  -> Protocol Router / Adapter
  -> Agent Runtime
  -> Artifact + Audit + EventBus
```

The project now keeps the original evaluation framework while adding a runtime
surface for interactive tasks. Existing risk tests still use `ExperimentRunner`,
`Gateway`, `Registry`, protocol adapters, and audit logs.

## Agent Runtime

`src/runtime/` defines:

- `AgentInvocation`
- `AgentInvocationResult`
- `AgentRuntime`
- `AgentRuntimeManager`
- `AG2AgentRuntime`
- `HTTPAgentRuntime`
- `LLMAgentRuntime`
- `HumanAgentRuntime`

Existing `IoAAgent.run_task()` implementations are wrapped by `AG2AgentRuntime`.
`IoAEnvironment.run_agent_task()` remains available for older endpoint and test
paths, but it now delegates to `runtime_manager` when a runtime is bound.

## ToolGateway

`src/tools/` adds a structured Agent-to-Tool boundary. Local demo tools from
`src/agents/tools.py` are registered by `build_default_tool_gateway()`.
Tool descriptors can be loaded from `config/tools.yaml`.

Tool calls use:

```python
ToolCall(tool_id="get_stock_price", arguments={"ticker": "AAPL"}, granted_scopes=["read_market_data"])
```

High-risk tools require the deterministic `high_risk_tool` scope. Critical
tools also require `critical_tool`. Tool input schemas are validated before
execution, and persisted tool history redacts sensitive argument keys.

## MCP Server Integration

`src/mcp/` adds:

- MCP server configuration models
- YAML-backed `MCPServerRegistry`
- JSON-RPC HTTP `MCPClient`
- `MCPToolProvider`

MCP tools are synced into ToolGateway with IDs like:

```text
mcp:{server_id}:{tool_name}
```

MCP remains Agent-to-Tool only. Agent-to-Agent traffic continues to use A2A or
Private API routes.

## Task API

Interactive tasks are available through:

- `POST /api/tasks`
- `GET /api/tasks/{task_id}`
- `GET /api/tasks/{task_id}/events`
- `GET /api/tasks/{task_id}/tool-calls`
- `GET /api/tasks/{task_id}/artifacts`
- `GET /api/tasks/{task_id}/execution-graph`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/retry`
- `POST /api/tasks/{task_id}/feedback`

Prompt-only example:

```json
{
  "prompt": "请结合苹果公司的财务情况和最近相关新闻，分析短期投资风险，并给出结构化报告。",
  "constraints": {"require_citations": true}
}
```

## Orchestration

`src/orchestration/agentic_orchestrator.py` is the default task control plane.
It runs a capability-level DAG produced by `AgenticOrchestrationPlanner`.
Planner nodes start with `assigned_agent_id=None`; concrete Agents are bound at
runtime after Registry semantic discovery, verification, Gateway policy checks,
and protocol negotiation.

`ExecutionGraph` adds an observable graph made of verify, agent, and aggregate
nodes. The graph is available through `/api/tasks/{task_id}/execution-graph`
and is shown in the frontend task detail view.

## Trace And EventBus

`src/audit/event_bus.py` stores task events for frontend timeline views.
Gateway emits events for intake, policy, discovery, planning, delivery, and
completion. Events can be queried through task or trace APIs.

## Persistence

`src/persistence/` provides SQLite-backed stores for:

- tasks
- events
- tool calls
- artifacts

The development default is `data/ioa_runtime.sqlite3`, configured by
`config/storage.yaml`.

## Background Task Runner

`src/tasks/` adds an in-process queue and runner. `POST /api/tasks` accepts
`async_mode`; when true, the task is persisted as queued and completed by a
background runner. Cancel and retry APIs use the runtime task state machine.

## Protocol Boundary

`src/protocol/router.py` makes the boundary explicit:

- A2A, MCP, and Private API are negotiated by Gateway for registered Agents.
- Tool calls must go through ToolGateway.
- Direct Agent endpoint calls and raw Python tool registration are not part of
  the default agentic path.

The legacy MCP adapter remains available for controlled protocol-interop risk
tests.

## Agentic Demo

```powershell
.\.venv\Scripts\python.exe scripts\run_agentic_demo.py --prompt "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较旅行保险；任何购买必须先确认。" --offline-deterministic
```

The demo prints TaskSpec capabilities, initial plan, runtime Agent/domain
bindings, human checkpoints, tool calls, final answer, and the saved
trace/evidence JSON path.

## Add An Agent

Use `POST /api/agents/onboard` with an `AgentCard` JSON payload. The first
onboarded status is `suspended`; call `POST /api/agents/{agent_id}/activate`
after verification.

## Add A Tool

Register a `ToolDescriptor` plus a callable in `ToolRegistry`, then call it
through `ToolGateway.call_tool()`. MCP tools should be exposed through
`MCPToolProvider`, not by bypassing ToolGateway.

## Frontend Operations

The frontend now includes:

- IoA workspace
- Task detail with execution graph, events, tool calls, artifacts, and controls
- Agent registry
- Tool registry
- MCP server registry

## End-To-End Demo

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

Open the frontend and use the `IoA 工作台` tab, or call `POST /api/tasks`
directly with the JSON example above.

## Verification

Backend:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Frontend:

```powershell
cd frontend
npm install
npm run build
```
