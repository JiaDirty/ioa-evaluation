# IoA 安全测评与 Agent 运行框架

本项目用于构建和运行 IoA（多个 Agent 通过网络、工具和共享状态协作）安全测评。仓库同时保留两套测评：

1. 旧版 18 项系统机制测评，重点检查 Gateway、Registry、协议、权限和攻击注入等系统能力；
2. 八项 Agent 模型安全测评，重点记录真实模型在正常条件和风险条件下的输入、输出、工具动作、系统处理、Judge 判断与最终影响。

## 当前项目状态

状态更新时间：2026-08-05。

- 代码自动测试：457 项通过，另有 13 项子测试通过；
- 八项测评中的 CAS、CON、RUM、INC、JUD、NOR、AGE 已有正常阶段和风险阶段的真实运行记录；
- DIS（讨论扭曲）的最新风险阶段仍有工具证据编号和超长重复输出问题，尚未形成可采用的最终结果；
- 当前真实运行属于最小开发验证，不是 160 个案例的正式科研实验；
- 恢复阶段目前可以通过 `--skip-recovery` 临时跳过；
- `results/` 中的原始记录保留在本机，不进入 Git，因为文件量和体积较大，且部分失败记录仍需保留原路径复核。

更详细的进度与限制见 [资料导航](docs/README.md) 和 [运行结果索引](docs/运行结果索引.md)。

## 目录说明

| 目录 | 中文用途 |
|---|---|
| `src/` | 后端核心代码，包括 Agent、Gateway、运行环境、测评执行器、Judge 和记录导出 |
| `tests/` | 自动测试，检查代码行为和已修复问题是否重新出现 |
| `data/agent_model_cases/` | 八项 Agent 模型安全测评的 160 个案例 |
| `risk_tests/` | 旧版系统风险测试实现 |
| `scripts/` | 测评运行、数据校验、记录导出和 Judge 校准脚本 |
| `api/` | FastAPI 后端接口 |
| `frontend/` | React 前端运行控制台 |
| `config/` | Agent、Judge、工具、协议和存储配置示例 |
| `docs/` | 已按用途整理的中文项目资料 |
| `results/` | 本机运行结果，不上传 GitHub |
| `待删除/` | 暂时不删除但已退出当前主流程的历史资料 |

每个代码目录的详细职责见 [代码目录中文说明](docs/代码目录中文说明.md)。

## 安装

推荐使用项目自带的虚拟环境；如果需要重新创建：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

真实模型配置文件不会上传 GitHub。首次配置时复制示例文件：

```powershell
Copy-Item config\agent_llm_config_example.yaml config\agent_llm_config.yaml
Copy-Item config\judge_llm_config_example.yaml config\judge_llm_config.yaml
```

然后在本机配置模型、接口地址和 API Key。不要把真实 Key 提交到 Git。

## 常用验证命令

完整自动测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

检查 Python 文件是否存在语法错误：

```powershell
.\.venv\Scripts\python.exe -m compileall -q src scripts
```

只检查八项测评数据和配置，不调用真实模型：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_model_suite.py --validate-only
```

运行离线八项最小流程，不调用真实模型：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_model_suite.py `
  --run-purpose smoke `
  --execution-mode offline_deterministic `
  --repeat-count 1 `
  --skip-recovery
```

真实模型会产生费用。运行前必须确认配置、案例范围、阶段和输出目录。

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

## 重要边界

- 模型做出不安全判断是测评结果，程序不能把原回答修改成安全回答；
- 模型提出动作、系统是否实际执行、Judge 如何判断、最终状态是否受影响，必须分开记录；
- 自动测试通过只说明代码没有触发已知错误，不代表真实八项流程已经完整跑通；
- 单案例或单次最小运行不能替代完整 160 案例、重复运行、独立 Judge 和人工复核的正式实验。

## GitHub

远程仓库：`https://github.com/JiaDirty/ioa-evaluation`

当前开发分支：`codex/agentic-decision-agents`
