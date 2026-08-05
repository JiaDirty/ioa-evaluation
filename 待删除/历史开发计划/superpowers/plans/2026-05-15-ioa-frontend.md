# IOA 测评控制台 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 IOA 安全测评框架搭建 React + FastAPI 全栈前端，实现测试结果可视化和实验运行控制。

**Architecture:** FastAPI 作为 API 层桥接现有 Python 框架和 React 前端。前端通过 REST API 获取数据，WebSocket 接收实验实时进度。4 个 Tab 页面覆盖仪表盘、实验控制、反馈循环、Agent 拓扑。

**Tech Stack:** React 18, TypeScript, Vite, Recharts, React Flow, FastAPI, Uvicorn

---

## File Structure

### 后端 (api/)

| File | Responsibility |
|---|---|
| `api/main.py` | FastAPI app, CORS, 路由注册, 静态文件服务 |
| `api/schemas.py` | Pydantic 请求/响应模型 |
| `api/routes/experiments.py` | 实验报告 API + WebSocket 进度推送 |
| `api/routes/feedback.py` | 反馈循环 API |
| `api/routes/agents.py` | Agent 拓扑 API |

### 前端 (frontend/)

| File | Responsibility |
|---|---|
| `frontend/src/types/index.ts` | 所有 TypeScript 类型定义 |
| `frontend/src/api/client.ts` | API 调用封装 (fetch + WebSocket) |
| `frontend/src/hooks/useWebSocket.ts` | WebSocket hook |
| `frontend/src/hooks/useApi.ts` | 数据获取 hook |
| `frontend/src/components/Card.tsx` | 通用卡片组件 |
| `frontend/src/components/Badge.tsx` | 状态/风险等级标签 |
| `frontend/src/components/ProgressBar.tsx` | 进度条组件 |
| `frontend/src/components/DataTable.tsx` | 通用表格组件 |
| `frontend/src/components/TabNav.tsx` | 顶部标签导航 |
| `frontend/src/pages/Dashboard.tsx` | 测试仪表盘页面 |
| `frontend/src/pages/ExperimentControl.tsx` | 实验控制页面 |
| `frontend/src/pages/FeedbackLoop.tsx` | 反馈循环页面 |
| `frontend/src/pages/Topology.tsx` | Agent 拓扑页面 |
| `frontend/src/App.tsx` | 主应用 + Tab 路由 |
| `frontend/src/main.tsx` | 入口文件 |
| `frontend/src/App.css` | 全局样式 |
| `frontend/index.html` | HTML 模板 |
| `frontend/package.json` | 依赖配置 |
| `frontend/tsconfig.json` | TypeScript 配置 |
| `frontend/vite.config.ts` | Vite 配置 (含 API 代理) |

---

## Task 1: 项目脚手架 — FastAPI 后端

**Files:**
- Create: `api/main.py`
- Create: `api/schemas.py`
- Create: `api/__init__.py`
- Create: `api/routes/__init__.py`
- Create: `api/routes/experiments.py`
- Create: `api/routes/feedback.py`
- Create: `api/routes/agents.py`

- [ ] **Step 1: 安装 FastAPI 依赖**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建"
pip install fastapi uvicorn websockets
```

- [ ] **Step 2: 创建 api/schemas.py — Pydantic 模型**

```python
"""API 请求/响应模型。"""

from __future__ import annotations

from pydantic import BaseModel
from typing import Any, Optional


class ExperimentRunRequest(BaseModel):
    mode: str = "all"  # "all" | "category" | "single"
    category: Optional[str] = None
    test_id: Optional[str] = None
    topology: str = "full_mesh"


class ExperimentRunResponse(BaseModel):
    experiment_id: str
    status: str


class ReportSummary(BaseModel):
    experiment_id: str
    timestamp: str
    total_tests: int
    passed: int
    failed: int


class TopologyUpdate(BaseModel):
    style: str  # "full_mesh" | "star" | "chain"
```

- [ ] **Step 3: 创建 api/routes/experiments.py**

```python
"""实验相关 API 路由。"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..schemas import ExperimentRunRequest, ExperimentRunResponse, ReportSummary

router = APIRouter(prefix="/api/experiments", tags=["experiments"])

# 存储运行中的实验
_active_experiments: dict[str, dict] = {}


@router.get("/reports")
async def list_reports() -> list[dict]:
    """列出已有报告。"""
    results_dir = Path(__file__).parent.parent.parent / "results"
    if not results_dir.exists():
        return []
    reports = []
    for f in sorted(results_dir.glob("experiment_report_*.json"), reverse=True):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
            reports.append({
                "id": f.stem,
                "timestamp": data.get("timestamp", ""),
                "total_tests": data.get("summary", {}).get("total_tests", 0),
                "passed": data.get("summary", {}).get("passed", 0),
                "failed": data.get("summary", {}).get("failed", 0),
            })
        except Exception:
            continue
    return reports


@router.get("/reports/{report_id}")
async def get_report(report_id: str) -> dict:
    """获取单个报告详情。"""
    results_dir = Path(__file__).parent.parent.parent / "results"
    filepath = results_dir / f"{report_id}.json"
    if not filepath.exists():
        return {"error": "Report not found"}
    with open(filepath, "r", encoding="utf-8") as f:
        return json.load(f)


@router.post("/run")
async def run_experiment(req: ExperimentRunRequest) -> ExperimentRunResponse:
    """触发实验运行。"""
    exp_id = f"exp-{uuid.uuid4().hex[:8]}"
    _active_experiments[exp_id] = {"status": "running", "current": 0, "total": 0}

    # 后台运行实验
    asyncio.create_task(_run_experiment_task(exp_id, req))

    return ExperimentRunResponse(experiment_id=exp_id, status="started")


async def _run_experiment_task(exp_id: str, req: ExperimentRunRequest):
    """后台执行实验。"""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))

    from src.experiment.runner import ExperimentRunner, IoAEnvironment
    from risk_tests.registry import ALL_TESTS, TESTS_BY_CATEGORY, get_test

    try:
        # 初始化环境
        env = IoAEnvironment()
        for sub_ioa_id in ["finance", "healthcare", "travel", "news"]:
            env.add_sub_ioa(sub_ioa_id)
        await env.setup_default_agents()
        await env.setup_default_topology(req.topology)

        runner = ExperimentRunner(env)

        # 确定测试列表
        if req.mode == "all":
            tests = ALL_TESTS
        elif req.mode == "category" and req.category:
            tests = TESTS_BY_CATEGORY.get(req.category, [])
        elif req.mode == "single" and req.test_id:
            t = get_test(req.test_id)
            tests = [t] if t else []
        else:
            tests = ALL_TESTS

        _active_experiments[exp_id]["total"] = len(tests)

        # 逐个运行测试
        for i, test in enumerate(tests):
            _active_experiments[exp_id]["current"] = i + 1
            _active_experiments[exp_id]["current_test"] = test.test_id

            result = await runner.run_single_test(test.test_id, test.run)

            # 存储结果供 WebSocket 推送
            if "results" not in _active_experiments[exp_id]:
                _active_experiments[exp_id]["results"] = []
            _active_experiments[exp_id]["results"].append({
                "test_id": result.test_id,
                "passed": result.passed,
                "risk_level": result.risk_level.value,
            })

            await asyncio.sleep(0.1)  # 让 WebSocket 有机会推送

        # 生成报告
        report = await runner.generate_report()
        _active_experiments[exp_id]["status"] = "completed"
        _active_experiments[exp_id]["report"] = report

        # 保存报告文件
        results_dir = Path(__file__).parent.parent.parent / "results"
        results_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = results_dir / f"experiment_report_{timestamp}.json"
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2, default=str)

    except Exception as e:
        _active_experiments[exp_id]["status"] = "failed"
        _active_experiments[exp_id]["error"] = str(e)


@router.websocket("/ws/{exp_id}/progress")
async def ws_progress(websocket: WebSocket, exp_id: str):
    """WebSocket 实时进度推送。"""
    await websocket.accept()

    if exp_id not in _active_experiments:
        await websocket.send_json({"type": "error", "message": "Experiment not found"})
        await websocket.close()
        return

    last_result_count = 0
    try:
        while True:
            exp = _active_experiments.get(exp_id, {})
            status = exp.get("status", "unknown")

            # 推送新结果
            results = exp.get("results", [])
            if len(results) > last_result_count:
                for r in results[last_result_count:]:
                    await websocket.send_json({"type": "result", **r})
                last_result_count = len(results)

            # 推送进度
            await websocket.send_json({
                "type": "progress",
                "current": exp.get("current", 0),
                "total": exp.get("total", 0),
                "test_id": exp.get("current_test", ""),
                "status": status,
            })

            if status == "completed":
                await websocket.send_json({"type": "complete", "report": exp.get("report", {})})
                break
            elif status == "failed":
                await websocket.send_json({"type": "error", "message": exp.get("error", "Unknown error")})
                break

            await asyncio.sleep(0.5)

    except WebSocketDisconnect:
        pass
```

- [ ] **Step 4: 创建 api/routes/feedback.py**

```python
"""反馈循环 API 路由。"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


def _load_latest_report() -> dict | None:
    results_dir = Path(__file__).parent.parent.parent / "results"
    if not results_dir.exists():
        return None
    files = sorted(results_dir.glob("experiment_report_*.json"), reverse=True)
    if not files:
        return None
    with open(files[0], "r", encoding="utf-8") as f:
        return json.load(f)


@router.get("/summary")
async def get_feedback_summary() -> dict:
    """获取反馈循环摘要。"""
    report = _load_latest_report()
    if not report:
        return {"error": "No report found"}
    return report.get("feedback_loop", {})


@router.get("/actions")
async def get_feedback_actions() -> list[dict]:
    """获取反馈动作列表。"""
    report = _load_latest_report()
    if not report:
        return []
    return report.get("feedback_actions", [])
```

- [ ] **Step 5: 创建 api/routes/agents.py**

```python
"""Agent 拓扑 API 路由。"""

from __future__ import annotations

from fastapi import APIRouter

from ...src.agents.ioa_agent import SUB_IOA_AGENT_CONFIGS

router = APIRouter(prefix="/api/agents", tags=["agents"])

# 全局拓扑状态（简单实现）
_current_topology = {"style": "full_mesh", "edges": []}


def _build_topology(style: str) -> dict:
    nodes = list(SUB_IOA_AGENT_CONFIGS.keys())
    edges = []
    if style == "full_mesh":
        for i, a in enumerate(nodes):
            for b in nodes[i + 1:]:
                edges.append({"source": a, "target": b})
    elif style == "star":
        center = nodes[0]
        for b in nodes[1:]:
            edges.append({"source": center, "target": b})
    elif style == "chain":
        for i in range(len(nodes) - 1):
            edges.append({"source": nodes[i], "target": nodes[i + 1]})
    return {"style": style, "nodes": nodes, "edges": edges}


@router.get("/sub-ioas")
async def list_sub_ioas() -> list[dict]:
    """列出所有 Sub-IoA 及其 Agent 信息。"""
    result = []
    for sub_ioa_id, cfg in SUB_IOA_AGENT_CONFIGS.items():
        result.append({
            "id": sub_ioa_id,
            "name": cfg["display_name"],
            "agent_name": cfg["name"],
            "capabilities": cfg["capabilities"],
        })
    return result


@router.get("/topology")
async def get_topology() -> dict:
    """获取当前拓扑结构。"""
    return _build_topology(_current_topology["style"])


@router.put("/topology")
async def update_topology(style: str = "full_mesh") -> dict:
    """修改拓扑模式。"""
    _current_topology["style"] = style
    return _build_topology(style)
```

- [ ] **Step 6: 创建 api/main.py**

```python
"""FastAPI 主应用。"""

from __future__ import annotations

import sys
from pathlib import Path

# 确保项目根目录在 path 中
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .routes import experiments, feedback, agents

app = FastAPI(title="IOA 测评控制台 API", version="1.0.0")

# CORS — 允许前端开发服务器
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(experiments.router)
app.include_router(feedback.router)
app.include_router(agents.router)


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "ioa-console-api"}


# 静态文件服务（生产模式）
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="static")
```

- [ ] **Step 7: 创建 __init__.py 文件**

```bash
touch api/__init__.py
touch api/routes/__init__.py
```

- [ ] **Step 8: 验证 FastAPI 启动**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000 --reload
# 浏览器访问 http://127.0.0.1:8000/docs 查看 Swagger UI
# 访问 http://127.0.0.1:8000/api/health 验证健康检查
```

---

## Task 2: 项目脚手架 — React 前端

**Files:**
- Create: `frontend/package.json`
- Create: `frontend/tsconfig.json`
- Create: `frontend/vite.config.ts`
- Create: `frontend/index.html`
- Create: `frontend/src/main.tsx`
- Create: `frontend/src/App.tsx`
- Create: `frontend/src/App.css`
- Create: `frontend/src/vite-env.d.ts`

- [ ] **Step 1: 创建 frontend/package.json**

```json
{
  "name": "ioa-console",
  "private": true,
  "version": "1.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc && vite build",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^18.3.1",
    "react-dom": "^18.3.1",
    "recharts": "^2.15.0",
    "@xyflow/react": "^12.4.0"
  },
  "devDependencies": {
    "@types/react": "^18.3.12",
    "@types/react-dom": "^18.3.1",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "^5.6.3",
    "vite": "^6.0.0"
  }
}
```

- [ ] **Step 2: 创建 frontend/tsconfig.json**

```json
{
  "compilerOptions": {
    "target": "ES2020",
    "useDefineForClassFields": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "skipLibCheck": true,
    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "isolatedModules": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": false,
    "noUnusedParameters": false,
    "noFallthroughCasesInSwitch": true,
    "forceConsistentCasingInFileNames": true
  },
  "include": ["src"]
}
```

- [ ] **Step 3: 创建 frontend/vite.config.ts**

```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
```

- [ ] **Step 4: 创建 frontend/index.html**

```html
<!DOCTYPE html>
<html lang="zh-CN">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>IOA 安全测评控制台</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: 创建 frontend/src/vite-env.d.ts**

```ts
/// <reference types="vite/client" />
```

- [ ] **Step 6: 创建 frontend/src/main.tsx**

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './App.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
)
```

- [ ] **Step 7: 创建 frontend/src/App.css — 全局样式（亮色学术风）**

```css
:root {
  --bg-primary: #ffffff;
  --bg-secondary: #f6f8fa;
  --border: #d0d7de;
  --text-primary: #1f2328;
  --text-secondary: #656d76;
  --color-blue: #0969da;
  --color-green: #1a7f37;
  --color-red: #cf222e;
  --color-yellow: #e3b341;
  --color-blue-light: #ddf4ff;
  --color-green-light: #f0fff4;
  --color-red-light: #fff5f5;
  --radius: 8px;
}

* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  background: var(--bg-secondary);
  color: var(--text-primary);
  line-height: 1.5;
}

.app {
  max-width: 1400px;
  margin: 0 auto;
  padding: 20px;
}

.app-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}

.app-header h1 {
  font-size: 22px;
  font-weight: 700;
  color: var(--text-primary);
}

.app-header .subtitle {
  font-size: 13px;
  color: var(--text-secondary);
}

/* Tab Navigation */
.tab-nav {
  display: flex;
  gap: 4px;
  border-bottom: 1px solid var(--border);
  margin-bottom: 24px;
}

.tab-nav button {
  padding: 10px 20px;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  font-size: 14px;
  color: var(--text-secondary);
  cursor: pointer;
  transition: all 0.2s;
}

.tab-nav button:hover {
  color: var(--text-primary);
}

.tab-nav button.active {
  color: var(--color-blue);
  border-bottom-color: var(--color-blue);
  font-weight: 600;
}

/* Cards */
.card {
  background: var(--bg-primary);
  border: 1px solid var(--border);
  border-radius: var(--radius);
  padding: 16px;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
  margin-bottom: 12px;
  color: var(--text-primary);
}

/* Grid */
.grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }

/* Badge */
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 11px;
  font-weight: 600;
}

.badge-pass { background: var(--color-green); color: #fff; }
.badge-fail { background: var(--color-red); color: #fff; }
.badge-high { color: var(--color-red); font-weight: 600; }
.badge-medium { color: var(--color-yellow); font-weight: 600; }
.badge-low { color: var(--color-green); font-weight: 600; }

/* Table */
table {
  width: 100%;
  border-collapse: collapse;
  font-size: 13px;
}

th {
  text-align: left;
  padding: 8px;
  color: var(--text-secondary);
  border-bottom: 1px solid var(--border);
  background: var(--bg-secondary);
  font-weight: 600;
}

td {
  padding: 8px;
  border-bottom: 1px solid #eee;
}

tr:hover td {
  background: var(--bg-secondary);
}

/* Progress Bar */
.progress-bar {
  background: var(--bg-secondary);
  border-radius: 4px;
  height: 24px;
  overflow: hidden;
}

.progress-bar-fill {
  height: 100%;
  background: var(--color-blue);
  border-radius: 4px;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #fff;
  font-size: 11px;
  font-weight: 600;
  transition: width 0.3s;
}

/* Log */
.log-container {
  background: var(--bg-secondary);
  border-radius: 6px;
  padding: 12px;
  font-family: monospace;
  font-size: 12px;
  max-height: 300px;
  overflow-y: auto;
  line-height: 1.8;
}

.log-pass { color: var(--color-green); }
.log-fail { color: var(--color-red); }

/* Button */
.btn-primary {
  background: var(--color-blue);
  color: #fff;
  border: none;
  padding: 10px 24px;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: opacity 0.2s;
}

.btn-primary:hover {
  opacity: 0.9;
}

.btn-primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* Pill selector */
.pill-group {
  display: flex;
  gap: 8px;
}

.pill {
  padding: 4px 12px;
  border-radius: 16px;
  font-size: 12px;
  cursor: pointer;
  border: 1px solid var(--border);
  background: var(--bg-secondary);
  color: var(--text-secondary);
  transition: all 0.2s;
}

.pill.active {
  background: var(--color-blue-light);
  border-color: var(--color-blue);
  color: var(--color-blue);
}

/* Feedback cards */
.feedback-card {
  padding: 8px;
  border-radius: 6px;
  border-left: 3px solid;
}

.feedback-critical { background: var(--color-red-light); border-color: var(--color-red); }
.feedback-high { background: #fff8f0; border-color: var(--color-yellow); }
.feedback-medium { background: #fff8f0; border-color: var(--color-yellow); }
.feedback-low { background: var(--color-green-light); border-color: var(--color-green); }

/* Responsive */
@media (max-width: 768px) {
  .grid-4 { grid-template-columns: repeat(2, 1fr); }
  .grid-2 { grid-template-columns: 1fr; }
}
```

- [ ] **Step 8: 创建 frontend/src/App.tsx — 主应用骨架**

```tsx
import { useState } from 'react'

const TABS = [
  { id: 'dashboard', label: '📊 测试仪表盘' },
  { id: 'experiment', label: '🧪 实验控制' },
  { id: 'feedback', label: '🔄 反馈循环' },
  { id: 'topology', label: '🕸️ Agent 拓扑' },
]

function App() {
  const [activeTab, setActiveTab] = useState('dashboard')

  return (
    <div className="app">
      <div className="app-header">
        <div>
          <h1>IOA 安全测评控制台</h1>
          <div className="subtitle">Internet of Agents Security Evaluation Console</div>
        </div>
      </div>

      <div className="tab-nav">
        {TABS.map(tab => (
          <button
            key={tab.id}
            className={activeTab === tab.id ? 'active' : ''}
            onClick={() => setActiveTab(tab.id)}
          >
            {tab.label}
          </button>
        ))}
      </div>

      <div className="tab-content">
        {activeTab === 'dashboard' && <div>Dashboard — TODO</div>}
        {activeTab === 'experiment' && <div>Experiment — TODO</div>}
        {activeTab === 'feedback' && <div>Feedback — TODO</div>}
        {activeTab === 'topology' && <div>Topology — TODO</div>}
      </div>
    </div>
  )
}

export default App
```

- [ ] **Step 9: 安装依赖并验证前端启动**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建/frontend"
npm install
npm run dev
# 浏览器访问 http://localhost:5173 查看骨架页面
```

---

## Task 3: TypeScript 类型定义 + API 客户端

**Files:**
- Create: `frontend/src/types/index.ts`
- Create: `frontend/src/api/client.ts`
- Create: `frontend/src/hooks/useApi.ts`
- Create: `frontend/src/hooks/useWebSocket.ts`

- [ ] **Step 1: 创建 frontend/src/types/index.ts**

```ts
// 实验报告相关
export interface ReportSummary {
  id: string
  timestamp: string
  total_tests: number
  passed: number
  failed: number
}

export interface TestResult {
  test_id: string
  test_name: string
  category: string
  passed: boolean
  risk_level: 'HIGH' | 'MEDIUM' | 'LOW'
  confidence: number
  explanation: string
  metrics: Record<string, number>
  details: Record<string, unknown>
  execution_time: number
}

export interface CategoryBreakdown {
  total: number
  passed: number
  failed: number
  tests: Pick<TestResult, 'test_id' | 'passed' | 'risk_level' | 'metrics'>[]
}

export interface ExperimentReport {
  timestamp: string
  summary: {
    total_tests: number
    passed: number
    failed: number
    utility: number
    audit_metrics: {
      chain_completeness: number
      attribution_accuracy: number
      source_coverage: number
      total_entries: number
      total_traces: number
    }
  }
  category_breakdown: Record<string, CategoryBreakdown>
  test_results: TestResult[]
  task_results: unknown[]
  feedback_loop?: FeedbackSummary
  feedback_actions?: FeedbackAction[]
}

// 反馈循环相关
export interface FeedbackSummary {
  total_tests: number
  total_passed: number
  total_failed: number
  dimensions: Record<string, {
    name: string
    risk_level: string
    pass_rate: string
    high_risk_tests: string[]
    recommendations: string[]
  }>
  feedback_actions: number
  critical_actions: number
}

export interface FeedbackAction {
  action_id: string
  source_test_id: string
  dimension: string
  action_type: string
  description: string
  priority: 'critical' | 'high' | 'medium' | 'low'
}

// Agent 相关
export interface SubIoA {
  id: string
  name: string
  agent_name: string
  capabilities: string[]
}

export interface TopologyData {
  style: string
  nodes: string[]
  edges: { source: string; target: string }[]
}

// WebSocket 消息
export interface WSProgressMessage {
  type: 'progress'
  current: number
  total: number
  test_id: string
  status: string
}

export interface WSResultMessage {
  type: 'result'
  test_id: string
  passed: boolean
  risk_level: string
}

export interface WSCompleteMessage {
  type: 'complete'
  report: ExperimentReport
}

export interface WSErrorMessage {
  type: 'error'
  message: string
}

export type WSMessage = WSProgressMessage | WSResultMessage | WSCompleteMessage | WSErrorMessage
```

- [ ] **Step 2: 创建 frontend/src/api/client.ts**

```ts
import type { ExperimentReport, ReportSummary, FeedbackSummary, FeedbackAction, SubIoA, TopologyData } from '../types'

const BASE = ''

async function fetchJSON<T>(url: string): Promise<T> {
  const res = await fetch(`${BASE}${url}`)
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${url}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}

// Experiments
export const listReports = () => fetchJSON<ReportSummary[]>('/api/experiments/reports')
export const getReport = (id: string) => fetchJSON<ExperimentReport>(`/api/experiments/reports/${id}`)
export const runExperiment = (body: { mode: string; category?: string; test_id?: string; topology?: string }) =>
  postJSON<{ experiment_id: string; status: string }>('/api/experiments/run', body)

// Feedback
export const getFeedbackSummary = () => fetchJSON<FeedbackSummary>('/api/feedback/summary')
export const getFeedbackActions = () => fetchJSON<FeedbackAction[]>('/api/feedback/actions')

// Agents
export const getSubIoAs = () => fetchJSON<SubIoA[]>('/api/agents/sub-ioas')
export const getTopology = () => fetchJSON<TopologyData>('/api/agents/topology')
export const updateTopology = (style: string) =>
  fetch(`${BASE}/api/agents/topology?style=${style}`, { method: 'PUT' }).then(r => r.json()) as Promise<TopologyData>
```

- [ ] **Step 3: 创建 frontend/src/hooks/useApi.ts**

```ts
import { useState, useEffect, useCallback } from 'react'

export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const reload = useCallback(() => {
    setLoading(true)
    setError(null)
    fetcher()
      .then(setData)
      .catch(e => setError(e.message))
      .finally(() => setLoading(false))
  }, deps)

  useEffect(reload, [reload])

  return { data, loading, error, reload }
}
```

- [ ] **Step 4: 创建 frontend/src/hooks/useWebSocket.ts**

```ts
import { useState, useEffect, useRef, useCallback } from 'react'
import type { WSMessage } from '../types'

export function useWebSocket(expId: string | null) {
  const [messages, setMessages] = useState<WSMessage[]>([])
  const [connected, setConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)

  const connect = useCallback(() => {
    if (!expId) return

    const ws = new WebSocket(`ws://${window.location.host}/ws/experiments/${expId}/progress`)
    wsRef.current = ws

    ws.onopen = () => setConnected(true)
    ws.onclose = () => setConnected(false)
    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data) as WSMessage
        setMessages(prev => [...prev, msg])
      } catch { /* ignore */ }
    }
  }, [expId])

  useEffect(() => {
    connect()
    return () => {
      wsRef.current?.close()
    }
  }, [connect])

  const reset = useCallback(() => setMessages([]), [])

  return { messages, connected, reset }
}
```

---

## Task 4: 通用组件

**Files:**
- Create: `frontend/src/components/Card.tsx`
- Create: `frontend/src/components/Badge.tsx`
- Create: `frontend/src/components/ProgressBar.tsx`
- Create: `frontend/src/components/DataTable.tsx`

- [ ] **Step 1: 创建 frontend/src/components/Card.tsx**

```tsx
import type { ReactNode } from 'react'

interface CardProps {
  title?: string
  children: ReactNode
  className?: string
}

export function Card({ title, children, className = '' }: CardProps) {
  return (
    <div className={`card ${className}`}>
      {title && <div className="card-title">{title}</div>}
      {children}
    </div>
  )
}
```

- [ ] **Step 2: 创建 frontend/src/components/Badge.tsx**

```tsx
interface BadgeProps {
  type: 'pass' | 'fail' | 'high' | 'medium' | 'low'
  children: React.ReactNode
}

export function Badge({ type, children }: BadgeProps) {
  return <span className={`badge badge-${type}`}>{children}</span>
}
```

- [ ] **Step 3: 创建 frontend/src/components/ProgressBar.tsx**

```tsx
interface ProgressBarProps {
  current: number
  total: number
}

export function ProgressBar({ current, total }: ProgressBarProps) {
  const pct = total > 0 ? (current / total) * 100 : 0
  return (
    <div className="progress-bar">
      <div className="progress-bar-fill" style={{ width: `${pct}%` }}>
        {current}/{total}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: 创建 frontend/src/components/DataTable.tsx**

```tsx
interface Column<T> {
  key: string
  header: string
  render: (row: T) => React.ReactNode
}

interface DataTableProps<T> {
  columns: Column<T>[]
  data: T[]
  getRowKey: (row: T) => string
}

export function DataTable<T>({ columns, data, getRowKey }: DataTableProps<T>) {
  return (
    <table>
      <thead>
        <tr>
          {columns.map(col => <th key={col.key}>{col.header}</th>)}
        </tr>
      </thead>
      <tbody>
        {data.map(row => (
          <tr key={getRowKey(row)}>
            {columns.map(col => <td key={col.key}>{col.render(row)}</td>)}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
```

---

## Task 5: Tab 1 — 测试仪表盘

**Files:**
- Create: `frontend/src/pages/Dashboard.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 创建 frontend/src/pages/Dashboard.tsx**

```tsx
import { RadarChart, PolarGrid, PolarAngleAxis, PolarRadiusAxis, Radar, ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip, Legend } from 'recharts'
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { DataTable } from '../components/DataTable'
import { useApi } from '../hooks/useApi'
import { listReports, getReport } from '../api/client'
import { useState } from 'react'
import type { ExperimentReport, TestResult } from '../types'

const CATEGORY_NAMES: Record<string, string> = {
  trust_authorization: '信任授权',
  protocol_interop: '协议互操作',
  interconnection: '互联扩散',
  public_knowledge: '公共知识',
  power_imbalance: '权力失衡',
  human_agency: '人机能动性',
}

export function Dashboard() {
  const { data: reports } = useApi(() => listReports())
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const { data: report, loading } = useApi(
    () => selectedId ? getReport(selectedId) : reports?.[0] ? getReport(reports[0].id) : Promise.reject('No reports'),
    [selectedId, reports]
  )

  if (loading || !report) return <div>加载中...</div>

  const { summary, category_breakdown, test_results } = report

  // 雷达图数据
  const radarData = Object.entries(category_breakdown).map(([cat, data]) => ({
    dimension: CATEGORY_NAMES[cat] || cat,
    score: data.total > 0 ? Math.round((data.passed / data.total) * 100) : 0,
    fullMark: 100,
  }))

  // 柱状图数据
  const barData = Object.entries(category_breakdown).map(([cat, data]) => ({
    name: CATEGORY_NAMES[cat] || cat,
    通过: data.passed,
    失败: data.failed,
  }))

  const testColumns = [
    { key: 'test_id', header: '测试ID', render: (r: TestResult) => <code>{r.test_id}</code> },
    { key: 'test_name', header: '名称', render: (r: TestResult) => r.test_name },
    { key: 'category', header: '类别', render: (r: TestResult) => CATEGORY_NAMES[r.category] || r.category },
    {
      key: 'status', header: '状态',
      render: (r: TestResult) => <Badge type={r.passed ? 'pass' : 'fail'}>{r.passed ? 'PASS' : 'FAIL'}</Badge>,
    },
    {
      key: 'risk', header: '风险',
      render: (r: TestResult) => <Badge type={r.risk_level.toLowerCase() as 'high' | 'medium' | 'low'}>{r.risk_level}</Badge>,
    },
  ]

  return (
    <div>
      {/* 报告选择 */}
      {reports && reports.length > 1 && (
        <div style={{ marginBottom: 16 }}>
          <select value={selectedId || reports[0]?.id || ''} onChange={e => setSelectedId(e.target.value)}>
            {reports.map(r => <option key={r.id} value={r.id}>{r.id} ({r.timestamp})</option>)}
          </select>
        </div>
      )}

      {/* 概览卡片 */}
      <div className="grid-4" style={{ marginBottom: 20 }}>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-blue)' }}>{summary.total_tests}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>总测试数</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-green)' }}>{summary.passed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>通过</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-red)' }}>{summary.failed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>失败</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 28, fontWeight: 700, color: 'var(--color-blue)' }}>
              {summary.total_tests > 0 ? Math.round((summary.passed / summary.total_tests) * 100) : 0}%
            </div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>通过率</div>
          </div>
        </Card>
      </div>

      {/* 雷达图 + 柱状图 */}
      <div className="grid-2" style={{ marginBottom: 20 }}>
        <Card title="六维风险雷达">
          <ResponsiveContainer width="100%" height={300}>
            <RadarChart data={radarData}>
              <PolarGrid />
              <PolarAngleAxis dataKey="dimension" tick={{ fontSize: 12 }} />
              <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
              <Radar name="通过率" dataKey="score" stroke="#0969da" fill="#0969da" fillOpacity={0.3} />
            </RadarChart>
          </ResponsiveContainer>
        </Card>
        <Card title="风险类别分布">
          <ResponsiveContainer width="100%" height={300}>
            <BarChart data={barData}>
              <XAxis dataKey="name" tick={{ fontSize: 11 }} />
              <YAxis />
              <Tooltip />
              <Legend />
              <Bar dataKey="通过" stackId="a" fill="#1a7f37" />
              <Bar dataKey="失败" stackId="a" fill="#cf222e" />
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>

      {/* 测试明细表 */}
      <Card title="测试明细">
        <DataTable columns={testColumns} data={test_results} getRowKey={r => r.test_id} />
      </Card>
    </div>
  )
}
```

- [ ] **Step 2: 更新 App.tsx 引入 Dashboard**

将 `App.tsx` 中的 `{activeTab === 'dashboard' && <div>Dashboard — TODO</div>}` 替换为：

```tsx
import { Dashboard } from './pages/Dashboard'
// ...
{activeTab === 'dashboard' && <Dashboard />}
```

- [ ] **Step 3: 验证仪表盘页面**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建/frontend"
npm run dev
# 浏览器访问 http://localhost:5173，确认仪表盘展示正常
```

---

## Task 6: Tab 2 — 实验控制面板

**Files:**
- Create: `frontend/src/pages/ExperimentControl.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 创建 frontend/src/pages/ExperimentControl.tsx**

```tsx
import { useState } from 'react'
import { Card } from '../components/Card'
import { ProgressBar } from '../components/ProgressBar'
import { runExperiment } from '../api/client'
import { useWebSocket } from '../hooks/useWebSocket'
import type { WSResultMessage } from '../types'

const CATEGORIES = [
  { id: 'trust_authorization', label: 'C1: 信任与授权失灵' },
  { id: 'protocol_interop', label: 'C2: 协议互操作失配' },
  { id: 'interconnection', label: 'C3: 互联扩散与可推断性' },
  { id: 'public_knowledge', label: 'C4: 公共知识失真' },
  { id: 'power_imbalance', label: 'C5: 生态权力失衡' },
  { id: 'human_agency', label: 'C6: 人机能动性侵蚀' },
]

const TOPOLOGIES = ['full_mesh', 'star', 'chain']

export function ExperimentControl() {
  const [mode, setMode] = useState<'all' | 'category' | 'single'>('all')
  const [category, setCategory] = useState('trust_authorization')
  const [topology, setTopology] = useState('full_mesh')
  const [running, setRunning] = useState(false)
  const [expId, setExpId] = useState<string | null>(null)
  const [logs, setLogs] = useState<string[]>([])

  const { messages } = useWebSocket(expId)

  // 从 WebSocket 消息提取进度和日志
  const progress = messages.find(m => m.type === 'progress')
  const results = messages.filter((m): m is WSResultMessage => m.type === 'result')
  const isComplete = messages.some(m => m.type === 'complete')

  const handleRun = async () => {
    setRunning(true)
    setLogs([])
    try {
      const res = await runExperiment({ mode, category: mode === 'category' ? category : undefined, topology })
      setExpId(res.experiment_id)
    } catch (e: any) {
      setLogs(prev => [...prev, `错误: ${e.message}`])
      setRunning(false)
    }
  }

  // 当实验完成时停止 running 状态
  if (isComplete && running) {
    setRunning(false)
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16 }}>
      {/* 左侧配置面板 */}
      <Card title="实验配置">
        <div style={{ marginBottom: 12 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>运行模式</div>
          <div className="pill-group">
            {(['all', 'category', 'single'] as const).map(m => (
              <span key={m} className={`pill ${mode === m ? 'active' : ''}`} onClick={() => setMode(m)}>
                {m === 'all' ? '全部测试' : m === 'category' ? '按类别' : '单个测试'}
              </span>
            ))}
          </div>
        </div>

        {mode === 'category' && (
          <div style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>测试类别</div>
            <select value={category} onChange={e => setCategory(e.target.value)} style={{ width: '100%', padding: 8, borderRadius: 6, border: '1px solid var(--border)' }}>
              {CATEGORIES.map(c => <option key={c.id} value={c.id}>{c.label}</option>)}
            </select>
          </div>
        )}

        <div style={{ marginBottom: 16 }}>
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginBottom: 4 }}>拓扑模式</div>
          <div className="pill-group">
            {TOPOLOGIES.map(t => (
              <span key={t} className={`pill ${topology === t ? 'active' : ''}`} onClick={() => setTopology(t)}>
                {t === 'full_mesh' ? '全连接' : t === 'star' ? '星型' : '链式'}
              </span>
            ))}
          </div>
        </div>

        <button className="btn-primary" onClick={handleRun} disabled={running} style={{ width: '100%' }}>
          {running ? '⏳ 运行中...' : '▶ 运行实验'}
        </button>
      </Card>

      {/* 右侧运行状态 */}
      <div>
        <Card title="运行进度" style={{ marginBottom: 16 }}>
          <ProgressBar current={progress?.current || 0} total={progress?.total || 0} />
          <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 8 }}>
            {progress ? `当前: ${progress.test_id}` : '等待启动...'}
          </div>
        </Card>

        {/* 实时结果 */}
        {results.length > 0 && (
          <Card title="测试结果" style={{ marginBottom: 16 }}>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
              {results.map((r, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13 }}>
                  <span className={r.passed ? 'log-pass' : 'log-fail'}>
                    {r.passed ? '✓' : '✗'}
                  </span>
                  <code style={{ fontSize: 12 }}>{r.test_id}</code>
                  <span className={`badge badge-${r.risk_level.toLowerCase()}`}>{r.risk_level}</span>
                </div>
              ))}
            </div>
          </Card>
        )}

        <Card title="实时日志">
          <div className="log-container">
            {logs.length === 0 && !running && <div style={{ color: 'var(--text-secondary)' }}>运行实验后此处显示日志...</div>}
            {logs.map((log, i) => <div key={i}>{log}</div>)}
          </div>
        </Card>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: 更新 App.tsx 引入 ExperimentControl**

```tsx
import { ExperimentControl } from './pages/ExperimentControl'
// ...
{activeTab === 'experiment' && <ExperimentControl />}
```

---

## Task 7: Tab 3 — 反馈循环可视化

**Files:**
- Create: `frontend/src/pages/FeedbackLoopPage.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 创建 frontend/src/pages/FeedbackLoopPage.tsx**

```tsx
import { Card } from '../components/Card'
import { Badge } from '../components/Badge'
import { useApi } from '../hooks/useApi'
import { getFeedbackSummary, getFeedbackActions } from '../api/client'
import type { FeedbackSummary, FeedbackAction } from '../types'

const RISK_EMOJI: Record<string, string> = {
  HIGH: '🔴',
  MEDIUM: '🟡',
  LOW: '🟢',
}

export function FeedbackLoopPage() {
  const { data: summary } = useApi(() => getFeedbackSummary())
  const { data: actions } = useApi(() => getFeedbackActions())

  if (!summary) return <div>加载中...</div>

  const dimensions = Object.entries(summary.dimensions || {})

  return (
    <div>
      {/* 概览 */}
      <div className="grid-4" style={{ marginBottom: 20 }}>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-blue)' }}>{summary.total_tests}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>总测试</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-green)' }}>{summary.total_passed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>通过</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-red)' }}>{summary.total_failed}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>失败</div>
          </div>
        </Card>
        <Card>
          <div style={{ textAlign: 'center' }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: 'var(--color-red)' }}>{summary.critical_actions}</div>
            <div style={{ fontSize: 12, color: 'var(--text-secondary)' }}>关键反馈</div>
          </div>
        </Card>
      </div>

      <div className="grid-2" style={{ marginBottom: 20 }}>
        {/* 风险维度报告 */}
        <Card title="风险维度报告">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {dimensions.map(([key, dim]) => (
              <div key={key} style={{
                display: 'flex', alignItems: 'center', gap: 8, padding: 10,
                background: dim.risk_level === 'HIGH' ? 'var(--color-red-light)' : dim.risk_level === 'MEDIUM' ? '#fff8f0' : 'var(--color-green-light)',
                borderRadius: 6,
                border: `1px solid ${dim.risk_level === 'HIGH' ? 'var(--color-red)' : dim.risk_level === 'MEDIUM' ? 'var(--color-yellow)' : 'var(--color-green)'}`,
              }}>
                <span style={{ fontSize: 16 }}>{RISK_EMOJI[dim.risk_level] || '⚪'}</span>
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 13 }}>{dim.name}</div>
                  <div style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{dim.risk_level} — {dim.pass_rate} 通过</div>
                </div>
              </div>
            ))}
          </div>
        </Card>

        {/* 反馈动作 */}
        <Card title={`反馈动作 (${(actions || []).length} 项)`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {(actions || []).slice(0, 10).map(action => (
              <div key={action.action_id} className={`feedback-card feedback-${action.priority}`}>
                <div style={{ fontWeight: 600, fontSize: 12, color: action.priority === 'critical' ? 'var(--color-red)' : action.priority === 'high' ? 'var(--color-yellow)' : 'var(--text-secondary)' }}>
                  {action.priority.toUpperCase()}
                </div>
                <div style={{ fontSize: 12, marginTop: 4 }}>{action.description}</div>
              </div>
            ))}
            {(!actions || actions.length === 0) && <div style={{ color: 'var(--text-secondary)', fontSize: 13 }}>暂无反馈动作</div>}
          </div>
        </Card>
      </div>

      {/* 修复建议 */}
      <Card title="修复建议">
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
          {dimensions.flatMap(([key, dim]) =>
            dim.recommendations.map((rec, i) => (
              <div key={`${key}-${i}`} style={{ padding: 10, background: 'var(--color-blue-light)', borderRadius: 6, border: '1px solid #d0e3f7' }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--color-blue)' }}>{dim.name}</div>
                <div style={{ fontSize: 12, marginTop: 4 }}>{rec}</div>
              </div>
            ))
          )}
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 2: 更新 App.tsx 引入 FeedbackLoopPage**

```tsx
import { FeedbackLoopPage } from './pages/FeedbackLoopPage'
// ...
{activeTab === 'feedback' && <FeedbackLoopPage />}
```

---

## Task 8: Tab 4 — Agent 生态拓扑

**Files:**
- Create: `frontend/src/pages/Topology.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: 创建 frontend/src/pages/Topology.tsx**

```tsx
import { useCallback, useMemo } from 'react'
import { ReactFlow, Background, Controls, type Node, type Edge } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Card } from '../components/Card'
import { useApi } from '../hooks/useApi'
import { getSubIoAs, getTopology, updateTopology } from '../api/client'
import type { SubIoA } from '../types'

const COLORS: Record<string, string> = {
  finance: '#0969da',
  healthcare: '#1a7f37',
  travel: '#e3b341',
  news: '#8250df',
}

export function Topology() {
  const { data: subIoAs } = useApi(() => getSubIoAs())
  const { data: topology, reload } = useApi(() => getTopology())

  const nodes: Node[] = useMemo(() => {
    if (!subIoAs) return []
    const positions: Record<string, { x: number; y: number }> = {
      finance: { x: 200, y: 50 },
      healthcare: { x: 50, y: 200 },
      travel: { x: 350, y: 200 },
      news: { x: 200, y: 350 },
    }
    return subIoAs.map(s => ({
      id: s.id,
      position: positions[s.id] || { x: 0, y: 0 },
      data: { label: `${s.name}\n${s.agent_name}` },
      style: {
        background: '#ddf4ff',
        border: `2px solid ${COLORS[s.id] || '#0969da'}`,
        borderRadius: '50%',
        width: 100,
        height: 100,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        textAlign: 'center' as const,
        fontSize: 12,
        fontWeight: 600,
        color: COLORS[s.id] || '#0969da',
      },
    }))
  }, [subIoAs])

  const edges: Edge[] = useMemo(() => {
    if (!topology) return []
    return topology.edges.map((e, i) => ({
      id: `e-${i}`,
      source: e.source,
      target: e.target,
      style: { stroke: '#54aeff', strokeWidth: 2, strokeDasharray: '5,5' },
    }))
  }, [topology])

  const handleTopologyChange = async (style: string) => {
    await updateTopology(style)
    reload()
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '280px 1fr', gap: 16 }}>
      {/* 左侧 Sub-IoA 列表 */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        {(subIoAs || []).map(s => (
          <Card key={s.id}>
            <div style={{ fontWeight: 600, color: COLORS[s.id] || 'var(--color-blue)' }}>{s.name}</div>
            <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginTop: 4 }}>
              能力: {s.capabilities.slice(0, 3).join(', ')}...
            </div>
            <div style={{ fontSize: 11, color: 'var(--color-green)', marginTop: 2 }}>
              AG2: {s.agent_name} ✓
            </div>
          </Card>
        ))}

        <Card title="拓扑模式">
          <div className="pill-group" style={{ flexDirection: 'column' }}>
            {['full_mesh', 'star', 'chain'].map(t => (
              <span
                key={t}
                className={`pill ${topology?.style === t ? 'active' : ''}`}
                onClick={() => handleTopologyChange(t)}
                style={{ textAlign: 'center' }}
              >
                {t === 'full_mesh' ? '全连接' : t === 'star' ? '星型' : '链式'}
              </span>
            ))}
          </div>
        </Card>
      </div>

      {/* 右侧拓扑图 */}
      <Card title="拓扑图" style={{ minHeight: 500 }}>
        <div style={{ height: 460 }}>
          <ReactFlow nodes={nodes} edges={edges} fitView>
            <Background />
            <Controls />
          </ReactFlow>
        </div>
      </Card>
    </div>
  )
}
```

- [ ] **Step 2: 更新 App.tsx 引入 Topology**

```tsx
import { Topology } from './pages/Topology'
// ...
{activeTab === 'topology' && <Topology />}
```

---

## Task 9: 集成验证

- [ ] **Step 1: 启动后端**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建"
python -m uvicorn api.main:app --host 127.0.0.1 --port 8000
```

- [ ] **Step 2: 启动前端**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建/frontend"
npm run dev
```

- [ ] **Step 3: 验证每个 Tab**

- 浏览器访问 http://localhost:5173
- Tab 1: 确认雷达图、柱状图、测试表格正常渲染
- Tab 2: 点击"运行实验"，确认进度条和日志实时更新
- Tab 3: 确认反馈维度和修复建议显示
- Tab 4: 确认拓扑图可交互，切换拓扑模式生效

- [ ] **Step 4: 提交代码**

```bash
cd "d:/个人文件/学习文件/实习/IOA测评搭建"
git add api/ frontend/
git commit -m "feat: add IOA evaluation console frontend with FastAPI backend"
```
