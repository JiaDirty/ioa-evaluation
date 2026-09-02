# 正式架构

项目只有一条场景生产主线和一个控制器 `PipelineOrchestrator`。所有参考来源、候选来源和未来生成数据先成为轻量 `ScenarioTask`，再按顺序生成或提取 `ScenarioKernel`、`EffectSpec`，最后由本地确定性编译器生成 `CompiledCase`。

`models.py` 定义四层数据模型、状态和质量记录；`catalog.py` 是 11 个叶级测评分支及配额的唯一来源；`registry.py` 保存任务状态、事件和产物引用；`artifact_store.py` 负责原子写入和依赖引用；`generation.py` 负责 Provider、结构化输出、重试和修订；`compiler.py` 只做确定性编译；`validation.py` 执行结构、Oracle 和六路径检查；`evaluation.py` 执行离线运行与记录；`orchestrator.py` 只协调这些职责。

Registry 状态按阶段递进：`TASK_READY`、`KERNEL_DRAFT`、`KERNEL_NEEDS_REVISION`、`KERNEL_READY`、`EFFECT_DRAFT`、`EFFECT_NEEDS_REVISION`、`EFFECT_READY`、`COMPILED`、`PATH_VALID`、`RUNTIME_VALID`、`SEMANTIC_ACCEPTED`、`HUMAN_ACCEPTED`、`FROZEN`。`REJECTED`、`GENERATION_FAILED`、`VALIDATION_FAILED` 保存不可静默跳过的结果。上游版本变化只失效其下游产物。
