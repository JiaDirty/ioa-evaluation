# IoA 运行环境升级说明

本文记录项目从固定路线执行升级到自然语言驱动的 Agent 式运行环境后，各模块的职责和使用方法。

## 当前默认方式：只提交目标和约束

默认执行模式是 `execution_mode="agentic"`。普通调用方只需要提交 `prompt` 和可选的 `constraints`，不需要提供 `origin_sub_ioa`、`target_sub_ioas`、`required_capabilities`、`task_type`、具体 Agent 编号或 `hop_chain`。

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

执行顺序为：

```text
自然语言目标
  -> TaskSpecificationAgent 整理任务
  -> AgenticOrchestrationPlanner 制定能力计划
  -> PlanValidator 检查计划
  -> Gateway 发现、选择并验证 Agent
  -> Agent 与工具运行循环
  -> SynthesisAgent 汇总
  -> TaskResult + ExecutionGraph + EvidenceBundle
```

旧的固定路线方式只保留在 `execution_mode="scripted"` 中。事件、步骤、回放和前端运行控制台见 [运行记录与可观测性说明.md](运行记录与可观测性说明.md)。

## 总体结构

```text
用户或前端
  -> Task API
  -> Gateway
  -> Policy + Decision Agents
  -> Registry 发现
  -> Orchestration Plan
  -> Protocol Router / Adapter
  -> Agent Runtime
  -> Artifact + Audit + EventBus
```

升级后的运行环境没有删除原来的测评框架。旧风险测试仍可使用 `ExperimentRunner`、`Gateway`、`Registry`、协议适配器和审计记录。

## Agent 运行环境

`src/runtime/` 定义以下核心对象：

- `AgentInvocation`：一次 Agent 调用的完整输入；
- `AgentInvocationResult`：一次 Agent 调用的结果；
- `AgentRuntime`：运行环境统一接口；
- `AgentRuntimeManager`：管理不同运行环境；
- `AG2AgentRuntime`：AG2 Agent 的运行适配；
- `HTTPAgentRuntime`：通过 HTTP 调用 Agent；
- `LLMAgentRuntime`：直接调用模型；
- `HumanAgentRuntime`：需要人工参与的运行方式。

已有的 `IoAAgent.run_task()` 通过 `AG2AgentRuntime` 接入统一运行环境。旧接口仍然保留，但在已经绑定运行环境时会转交给 `runtime_manager`。

## ToolGateway

`src/tools/` 是 Agent 调用工具的统一边界。本地工具由 `build_default_tool_gateway()` 注册，其他工具可以从 `config/tools.yaml` 加载。

工具请求示例：

```python
ToolCall(
    tool_id="get_stock_price",
    arguments={"ticker": "AAPL"},
    granted_scopes=["read_market_data"],
)
```

高风险工具需要 `high_risk_tool` 权限，关键工具还需要 `critical_tool` 权限。程序会在执行前检查参数结构和权限，保存历史时会隐藏敏感参数。

## MCP 工具

`src/mcp/` 包含 MCP 配置、服务注册、JSON-RPC 客户端和工具提供器。同步到 ToolGateway 的工具编号格式为：

```text
mcp:{server_id}:{tool_name}
```

MCP 只用于 Agent 调用工具。Agent 之间仍使用 A2A 或 Private API 通信。详细规则见 [MCP集成说明.md](MCP集成说明.md)。

## 任务 API

主要接口包括：

- `POST /api/tasks`：提交任务；
- `GET /api/tasks/{task_id}`：查看任务；
- `GET /api/tasks/{task_id}/events`：查看事件；
- `GET /api/tasks/{task_id}/tool-calls`：查看工具调用；
- `GET /api/tasks/{task_id}/artifacts`：查看产物；
- `GET /api/tasks/{task_id}/execution-graph`：查看执行图；
- `POST /api/tasks/{task_id}/cancel`：取消任务；
- `POST /api/tasks/{task_id}/retry`：重新运行；
- `POST /api/tasks/{task_id}/feedback`：提交反馈。

## 编排与执行图

`src/orchestration/agentic_orchestrator.py` 是默认控制程序。Planner 先生成能力级执行图，具体 Agent 在 Registry 发现、身份验证、Gateway 权限检查和协议协商完成后才绑定。

`ExecutionGraph` 会保存验证节点、Agent 节点和汇总节点，可通过任务详情接口和前端页面查看。

## 事件与持久化

`src/audit/event_bus.py` 保存任务接收、权限判断、发现、规划、投递和完成等事件。

`src/persistence/` 使用 SQLite 保存：

- 任务；
- 事件；
- 工具调用；
- 产物。

开发环境默认数据库是 `data/ioa_runtime.sqlite3`，配置文件为 `config/storage.yaml`。数据库是本地运行产物，不上传 GitHub。

## 后台任务

`src/tasks/` 提供进程内队列和执行器。`POST /api/tasks` 设置 `async_mode=true` 时，任务先保存为排队状态，再由后台执行。取消和重试都通过统一任务状态处理。

## 通信边界

`src/protocol/router.py` 明确区分：

- Agent 之间通过 A2A、MCP 以外的允许协议或 Private API；
- Agent 调用工具必须经过 ToolGateway；
- 默认 Agent 式路径不能绕过 Gateway 直接调用 Agent 接口或直接注册任意 Python 工具；
- 旧 MCP 适配器只保留给受控的协议兼容风险测试。

## 离线演示

```powershell
.\.venv\Scripts\python.exe scripts\run_agentic_demo.py `
  --prompt "为下个月前往肯尼亚出差的公司高管安排行程，评估当地健康风险，并比较旅行保险；任何购买必须先确认。" `
  --offline-deterministic
```

演示会输出任务能力、初始计划、实际绑定、人工确认、工具调用、最终回答和保存的记录路径，不调用真实模型。

## 增加 Agent 或工具

增加 Agent：向 `POST /api/agents/onboard` 提交 `AgentCard`。新 Agent 初始为 `suspended`，验证后调用 `POST /api/agents/{agent_id}/activate`。

增加本地工具：向 `ToolRegistry` 注册 `ToolDescriptor` 和执行函数，再通过 `ToolGateway.call_tool()` 调用。MCP 工具应通过 `MCPToolProvider` 接入。

## 启动后端和前端

后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

前端：

```powershell
cd frontend
npm install
npm run dev
```

前端包含 IoA 工作台、任务详情、执行图、事件、工具调用、产物、Agent 注册、工具注册和 MCP 服务管理。

## 验证命令

后端完整测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

前端构建：

```powershell
cd frontend
npm install
npm run build
```
