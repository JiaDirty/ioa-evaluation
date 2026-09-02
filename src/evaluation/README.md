# Evaluation 模块

正式场景生产入口为 `scenario_generation.PipelineOrchestrator`，正式评测入口为 `scripts/run_evaluation.py`。运行数据必须携带 `generic_scoring_v1` 声明式判分契约。

`business_protocol/` 负责协议执行、工具环境和声明式判分；`scenario_generation/` 负责四层数据结构、Registry、状态机、生成提交、编译和产物保存。参考来源转换位于 `scripts/migrations/`，不属于生产运行路径。
