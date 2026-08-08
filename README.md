# IoA Agent 模型行为安全测评

本仓库当前主要研究：同一个大模型被实例化为不同业务角色 Agent 后，在多 Agent 协作、上下游交接、共享记录、工具调用、授权和长期交互中表现出的行为安全。

仓库同时保留旧版 18 项系统机制测评。旧版检查 Gateway、Registry、协议、权限和攻击注入等系统能力，与当前八项 Agent 模型测评属于两条独立路径，不能混合统计。

## 当前状态

状态更新时间：2026-08-08。

- 六字段自由业务决策协议及后台隐藏行为派生已经实现；
- Agent-model 测试为 269 项通过，另有 10 项子测试通过；
- 全量测试为 465 项通过，另有 13 项子测试通过；
- 160 条数据均可解析，CAS、CON、RUM、NOR、INC、JUD、DIS、AGE 各 20 条；
- 当前 160 条均属于开发期间已见数据，真正未见的 holdout 仍为 0；
- 最终方案中的完整真实业务工具链尚未全部落入代码，CAS 仍存在旧简化场景与传播判分问题；
- 现有真实运行属于开发烟测，不构成正式科研结果。

先阅读 [项目资料导航](docs/README.md) 和 [项目定位与阅读入口](docs/当前方案/项目定位与阅读入口.md)。

## 当前八项测评主链

```text
scripts/run_agent_model_suite.py
  -> AgentModelSuiteRunner
  -> 八类 category executor
  -> AgentModelStepExecutor
  -> IoAEnvironment / Gateway / Agent Runtime / ToolGateway
  -> Evidence / Judge / Trace Export
```

主要路径：

| 路径 | 用途 |
|---|---|
| `data/agent_model_cases/` | 八个分类案例文件，当前数据维护入口 |
| `data/generated/` | 由分类案例确定性生成的兼容合并文件，不手工编辑 |
| `data/calibration/` | Judge 校准样本和报告 |
| `src/evaluation/agent_model/` | 八项 Agent 模型测评实现 |
| `scripts/run_agent_model_suite.py` | 八项测评命令入口 |
| `tests/test_agent_model_*.py` | 八项测评回归测试 |
| `src/gateway/`、`src/runtime/` | 共享 IoA 执行底座 |
| `risk_tests/`、`run_experiment.py` | 旧版 18 项系统测评 |
| `docs/当前方案/` | 当前唯一文档入口 |

## 常用离线验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q tests -k "agent_model"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe scripts\run_agent_model_suite.py --validate-only
.\.venv\Scripts\python.exe -m compileall -q src scripts
```

运行离线最小流程：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_model_suite.py `
  --run-purpose smoke `
  --execution-mode offline_deterministic `
  --repeat-count 1 `
  --skip-recovery
```

真实模型运行会产生费用。运行前必须确认用例、阶段、重复次数、被测模型、Judge 和输出目录。

## 本机文件边界

以下内容不上传 GitHub：模型密钥配置、虚拟环境、前端依赖、运行数据库、原始运行结果、日志、个人资料和源码压缩备份。当前本机旧运行记录位于仓库同级目录 `IOA测评运行记录/`，旧数据库和本机资料位于 `IOA测评本地状态/`、`IOA测评本地归档/`；今后新运行默认写入仓库内被忽略的 `.local/`。

远程仓库：`https://github.com/JiaDirty/ioa-evaluation`

当前开发分支：`codex/agentic-decision-agents`
