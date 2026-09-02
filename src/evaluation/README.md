# Evaluation 模块

正式场景生产和评测入口均为 `scripts/run_pipeline.py`，控制器为 `scenario_generation.PipelineOrchestrator`。案例必须携带由本地编译器生成的声明式判分契约。

`business_protocol/` 负责协议执行、工具环境和声明式判分；`scenario_generation/` 负责四层数据结构、Registry、状态机、生成提交、编译、六路径校验、离线评测和产物保存。参考来源转换位于 `scripts/migrations/`，不属于生产运行路径。
