# IOA Agent 行为安全测评

本项目评估 Agent 在多角色业务流程中的信息传递、工具调用、状态变化和错误恢复。当前代码只保留一套正式生产架构：所有数据进入 `ScenarioTask`，依次形成 `ScenarioKernel`、`EffectSpec` 和 `CompiledCase`，状态和文件统一由 `PipelineRegistry` 管理。

原始 11 条参考场景保存在 `data/scenarios/`，440 条候选及来源记录保存在 `data/candidate_batches/批量生成-第01轮/`。它们均作为不可覆盖的来源数据保留。参考数据转换、Registry 版本升级等一次性工作位于 `scripts/migrations/`；历史验证工具位于 `scripts/verification/`。

## 正式架构

```text
ScenarioTask
  -> ScenarioKernel
  -> EffectSpec
       -> EFFECT_DRAFT
       -> EFFECT_NEEDS_REVISION
       -> EFFECT_READY
  -> CompiledCase
  -> PATH_VALID
  -> RUNTIME_VALID
  -> SEMANTIC_ACCEPTED
  -> HUMAN_ACCEPTED
  -> FROZEN
```

```text
src/evaluation/scenario_generation/
├── models.py          数据模型公开入口
├── registry.py        PipelineRegistry 和记录模型
├── state_machine.py   阶段与严格流转规则
├── artifact_store.py  文件哈希与依赖引用
├── orchestrator.py    PipelineOrchestrator
├── generation.py      Kernel/EffectSpec 生成和提交接口
├── compiler.py        编译逻辑
└── __init__.py        包级正式接口
```

正式生产控制器是 `PipelineOrchestrator`，正式命令是：

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py --root <生产目录> --process-all --process
.\.venv\Scripts\python.exe scripts\run_evaluation.py --validate-only --data <数据目录>
.\.venv\Scripts\python.exe scripts\run_inspect_evaluation.py --validate-only
```

真实模型调用必须显式提供 `--allow-live-api`。普通迁移、校验、测试和 Registry 操作不会发起模型请求。

## 状态与证据

每条任务使用独立目录，保存 `scenario_task.json`、`scenario_kernel.json`、`effect_spec.json`、`compiled_case.json`、质量阶段记录和 `lineage.json`。根目录只有一个 `registry.json`；每个引用包含相对路径、SHA-256、Schema 版本和依赖关系。上游产物变化后，下游产物必须失效并重新生成。

`COMPILED` 只表示编译完成，不代表场景质量合格。路径检查、运行检查、两个独立语义审核和人工决定必须逐级通过，最终才能进入 `FROZEN`。

## 数据边界

- `data/scenarios/`：11 条哈希锁定参考来源，不直接进入正式运行器。
- `data/candidate_batches/批量生成-第01轮/`：440 条原始候选和来源记录，不覆盖、不删除。
- `scripts/migrations/`：参考数据转换和 Registry 升级。
- `scripts/verification/`：迁移等价性等历史验证。
- `docs/history/`：迁移过程、旧设计和交接记录。

正式运行器只接受带 `generic_scoring_v1` 声明式判分契约的案例，不再包含按类别写死的判分分支。

## 开发验证

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q src scripts tests
git diff --check
```

代码测试通过不等于 440 条候选已经完成质量验收；代码架构、数据质量和实际模型运行结果必须分别报告。
