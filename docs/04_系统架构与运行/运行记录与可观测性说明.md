# IoA 统一可观测结构与运行控制台

## 1. 目标与边界

统一可观测层同时服务普通 IoA 任务、后续 8 项 Agent 模型测评和原有 18 项系统安全测评。它只记录系统实际发生的事件，不修改任务结果，也不把“流程运行过”解释为“测评通过”。

事件是不可修改的事实记录。执行步骤由事件按 `span_id` 聚合；执行图和交互图再由执行步骤投影生成。这样可以避免事件、步骤和图分别维护状态而产生冲突。

系统不会记录模型隐藏推理过程，也不会保存密钥、认证头和未脱敏的敏感字段。

## 2. 数据链路

```text
任务或实验
  -> Registry / Gateway / Policy / Protocol / Agent / LLM / Tool / Judge
  -> 统一 TaskEvent
  -> SQLite 事件事实表
  -> ExecutionSpan 与脱敏输入输出
  -> 执行图、交互图和任务观测快照
  -> REST 回放 + WebSocket 实时推送
  -> IoA 运行控制台
```

每条事件包含全局顺序号、任务与实验上下文、步骤及父步骤、组件身份、执行图节点、阶段、尝试次数、时间、输入、输出、上下游、错误和引用。较大的输入输出单独保存，并在步骤详情中按引用读取。

## 3. SQLite 增量结构

原有 `tasks`、`events`、`tool_calls` 和 `artifacts` 表继续保留。启动时只增量创建或补充以下结构，不删除历史数据：

| 表 | 用途 |
|---|---|
| `observability_sequence` | 为事件分配跨任务递增的全局顺序号 |
| `execution_spans` | 保存由事件聚合的执行步骤、父子关系、状态、耗时和输入输出引用 |
| `observation_payloads` | 保存脱敏、可截断的大体积输入输出 |

服务重启后，任务、事件、步骤、产物和工具调用均可从 SQLite 回放。

## 4. 公共接口

| 接口 | 作用 |
|---|---|
| `GET /api/system/graph` | 按当前运行环境返回动态 IoA 系统结构 |
| `GET /api/tasks/{task_id}/observability` | 返回任务、事件、步骤、执行图、交互边、工具和产物的完整快照 |
| `GET /api/tasks/{task_id}/spans` | 按顺序号读取执行步骤 |
| `GET /api/tasks/{task_id}/spans/{span_id}` | 读取单步详情和大体积输入输出 |
| `WS /api/tasks/{task_id}/stream` | 实时推送任务事件，支持 `after_sequence` 补发 |
| `WS /api/experiments/{experiment_id}/stream` | 实时推送实验及其子任务事件 |

旧的任务、实验进度和 18 项测评接口继续兼容。

## 5. 运行控制台

前端主入口为“**IoA 运行控制台**”。页面包括：

1. 顶部运行模式、任务启动、历史任务打开、状态和进度；
2. 左侧动态系统结构，展示 Sub-IoA、Gateway、Registry、Agent、Tool、MCP、知识、协议、审计、合成、Judge 和人工确认；
3. 中间执行图与交互数据流，节点状态来自真实事件投影；
4. 右侧步骤详情，展示输入、输出、上下游、耗时、重试和错误；
5. 底部事件时间线、Agent 消息、工具调用和 Artifact 流转。

最近打开的任务 ID 保存在浏览器本地状态中。刷新页面会继续连接运行中的任务，终态任务则从 SQLite 回放。

## 6. 启动与验证

```powershell
# 后端
.\.venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000

# 前端
cd frontend
npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:5173/`，进入“IoA 运行控制台”。“离线检查”用于确定性链路验收；“真实模型”要求运行环境已配置受支持模型供应商的 API Key。没有真实凭据时，不能把离线结果作为 Live Smoke 或真实模型测评证据。

自动验收命令：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
cd frontend
npm run build
```
