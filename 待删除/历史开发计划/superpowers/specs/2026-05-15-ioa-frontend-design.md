# IOA 测评控制台 — 前端设计文档

## 概述

为 IOA（智能体互联网）安全测评框架搭建 Web 前端，实现实验结果可视化和实验运行控制。

**定位**：实验控制台 — 既能查看结果，也能从前端触发实验运行。

## 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | React 18 + TypeScript + Vite | SPA，组件化开发 |
| 图表 | Recharts | 雷达图、柱状图、进度条 |
| 拓扑图 | React Flow | Sub-IoA 拓扑可视化 |
| 后端 API | FastAPI | REST + WebSocket |
| 样式 | CSS Modules / Tailwind | 亮色学术风格 |

## 页面结构

顶部标签页（Tab）切换 4 个模块：

### Tab 1: 测试仪表盘 (Dashboard)

- **概览卡片**：总测试数、通过数、失败数、通过率
- **六维风险雷达图**：Recharts RadarChart，6 个风险维度
- **风险类别分布**：堆叠柱状图，按类别展示通过/失败
- **测试明细表格**：18 行，列 = 测试ID、名称、类别、状态(PASS/FAIL)、风险等级(HIGH/MEDIUM/LOW)

### Tab 2: 实验控制 (Experiment Control)

- **左侧配置面板**：
  - 运行模式选择：全部测试 / 按类别 / 单个测试
  - 类别下拉选择（C1-C6）
  - 拓扑模式选择：全连接 / 星型 / 链式
  - 运行按钮
- **右侧运行状态**：
  - 进度条（当前/总数）
  - 实时日志流（WebSocket 推送）

### Tab 3: 反馈循环 (Feedback Loop)

- **风险维度报告**：6 个维度卡片，显示风险等级和通过率
- **反馈动作列表**：按优先级排序（CRITICAL > HIGH > MEDIUM > LOW）
- **修复建议**：每个失败测试的具体修复建议

### Tab 4: Agent 生态拓扑 (Topology)

- **左侧 Sub-IoA 列表**：4 个 Sub-IoA 卡片，显示 Agent 数量、能力、AG2 状态
- **右侧拓扑图**：React Flow 可视化 Sub-IoA 间连接关系

## 视觉风格

**亮色学术风**，适合论文截图和答辩演示：

- 背景: `#ffffff` / `#f6f8fa`
- 卡片: `#ffffff` + `border: 1px solid #d0d7de`
- 主色: `#0969da` (蓝)
- 成功: `#1a7f37` (绿)
- 危险: `#cf222e` (红)
- 警告: `#e3b341` (黄)
- 文字: `#1f2328` / `#656d76`

## FastAPI 接口

```
GET  /api/experiments/reports          # 已有报告列表
GET  /api/experiments/reports/{id}     # 单个报告详情
POST /api/experiments/run              # 触发实验运行
WS   /ws/experiments/{id}/progress     # WebSocket 实时进度

GET  /api/feedback/summary             # 反馈循环摘要
GET  /api/feedback/actions             # 反馈动作列表

GET  /api/agents/sub-ioas              # Sub-IoA 列表
GET  /api/agents/topology              # 拓扑结构
PUT  /api/agents/topology              # 修改拓扑

GET  /api/health                       # 健康检查
```

### POST /api/experiments/run 请求体

```json
{
  "mode": "all | category | single",
  "category": "trust_authorization",
  "test_id": "ioa_identity_spoofing",
  "topology": "full_mesh"
}
```

### WebSocket 推送格式

```json
{"type": "progress", "current": 12, "total": 18, "test_id": "ioa_cascade_propagation", "status": "running"}
{"type": "result", "test_id": "ioa_cascade_propagation", "passed": true, "risk_level": "LOW"}
{"type": "complete", "report": { ... }}
```

## 目录结构

```
IOA测评搭建/
├── frontend/                  # React 前端
│   ├── src/
│   │   ├── components/        # 通用组件（Card, Table, Badge, ProgressBar）
│   │   ├── pages/             # 4 个页面
│   │   │   ├── Dashboard.tsx
│   │   │   ├── ExperimentControl.tsx
│   │   │   ├── FeedbackLoop.tsx
│   │   │   └── Topology.tsx
│   │   ├── hooks/             # 自定义 hooks（useWebSocket, useApi）
│   │   ├── api/               # API 调用层
│   │   ├── types/             # TypeScript 类型定义
│   │   ├── App.tsx            # 主应用 + Tab 路由
│   │   └── main.tsx           # 入口
│   ├── package.json
│   └── vite.config.ts
├── api/                       # FastAPI 后端
│   ├── main.py                # FastAPI app + CORS + 静态文件
│   ├── routes/
│   │   ├── experiments.py     # 实验相关 API
│   │   ├── feedback.py        # 反馈循环 API
│   │   └── agents.py          # Agent 拓扑 API
│   └── schemas.py             # Pydantic 请求/响应模型
└── src/                       # 现有框架（不改动）
```

## 数据流

```
用户点击"运行实验"
  → 前端 POST /api/experiments/run
  → FastAPI 创建 asyncio.Task 调用 ExperimentRunner
  → WebSocket 推送进度（每个测试完成时）
  → 完成后返回完整报告
  → 前端更新仪表盘
```

## 依赖

### 前端 (package.json)

- react, react-dom
- recharts
- reactflow
- typescript, vite

### 后端 (requirements 追加)

- fastapi
- uvicorn
- websockets
