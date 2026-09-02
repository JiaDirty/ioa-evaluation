# 数据格式

`ScenarioTask` 只包含任务 ID、叶级分支、测评目标、安全机制要求、场景约束、禁用模式、去重约束和来源引用，不包含可运行案例。原始完整 JSON 只通过 `reference_material` 只读引用。

`ScenarioKernel` 描述业务场景、角色权限、初始事实、核心目标、因果变量、风险触发条件、安全和危险行为、风险后果与恢复逻辑。`EffectSpec` 描述工具参数 Schema、返回、状态变化、行为 Oracle 和绑定的 Kernel 哈希。`CompiledCase` 只能由本地编译器从已冻结的 Kernel 和 EffectSpec 生成。

每条任务的阶段产物位于 `data/workspace/cases/` 的独立目录，来源位于 `data/raw/`，状态位于 `data/workspace/registry.json`。发布数据位于 `data/releases/<release>/`。
