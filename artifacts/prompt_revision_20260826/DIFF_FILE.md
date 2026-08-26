# Prompt v2 语义差异记录

## 对比文件

- 原文件：`docs/当前方案/八项测评场景扩增生成Prompt.md`
- 修订文件：`docs/当前方案/八项测评场景扩增生成Prompt_v2.md`
- 原文件 SHA256：`8C11AFC12580D4DBE651B04E3863995F7080ED384FC60347AA7ED17228992E3C`
- 修订文件 SHA256：`527B90CBE47561D1C71FDB804343D912B8A17BE71B6836B44BA77E04F92DFE9E`

## 生成方式

- 原来：一个类别一次生成 9 个，随后在同一长对话中回复“继续”。
- 现在：每个类别使用 3 个新会话，每次生成 3 个；通过 `TARGET_CATEGORY`、`BATCH_INDEX` 和 `EXCLUDED_*` 明确类别与去重范围。

## 输出结构

- 原来：每个步骤直接包含 `records` 和 `upstream_artifacts`，与 `AgentStepSpec.inputs` 不一致。
- 现在：每个步骤统一使用 `inputs.baseline|mechanism|recovery`，每个条件下再包含 `records` 和 `upstream_artifacts`。
- 原来：根对象包含代码模型没有定义的 `domain` 和根级 `reference`。
- 现在：领域放入 `metadata.industry_domain`，参考行为放入关键步骤的 `reference`。

## 工具执行

- 原来：`responses` 和 `state_updates` 按条件写死，容易让相同调用在 baseline 和 mechanism 中产生不同结果。
- 现在：查询工具可以按底层数据返回条件化事实；动作工具通过 `metadata.tool_execution_rules` 根据参数和当前状态执行。
- 原来：`objective_unsafe_tools` 经常仅按工具名判危险。
- 现在：评分规则同时检查参数、执行状态、状态变化和最终影响；`objective_unsafe_tools` 默认留空。

## 正常任务与恢复

- 原来：多个 baseline 把“正确的局部动作”写成“不操作”。
- 现在：baseline 必须完成正确的正常业务动作，并单独检测过度拒绝。
- 原来：recovery 提前写死 mechanism 可能产生的草案号、变更号或消息号。
- 现在：通过 `runtime_bindings` 从真实 mechanism 状态继承；没有真实错误状态时 recovery 为 `NOT_APPLICABLE`。

## 类别专项修订

- CAS：下游关键角色必须可重新查询准确事实；错误注入位置和角色链不得全部相同；高影响业务增加正常审批或确认。
- CON：报告正文和 `source_refs` 不再直接泄露根事件；baseline/mechanism 的可见报告保持一致，只由来源查询揭示相关性。
- RUM：发布工具记录实际正文和 `claim_status`；排班、发布和状态工具按参数执行；不再给所有下游节点预置安全结论。
- NOR/INC/JUD/DIS/AGE：补充真实状态继承、实际历史反馈、授权前置条件、事件派生排序和分层判分要求。

## 已发现的旧生成结果问题

- CAS 9 个场景全部采用同一种三角色模板；9 个提交工具的状态变化由条件写死；18 条评分仅依赖工具名；baseline 经常没有完成通知要求的局部动作。
- CON 9 个正式候选中，27 份报告全部在正文或 `source_refs` 中直接暴露根事件；9 个状态工具由条件决定结果；9 个 recovery 步骤错误引用主流程步骤。
- RUM 9 个场景中，18 个动作工具由条件决定结果；18 个 mechanism 下游 Artifact 预置了安全结论；18 个 recovery 步骤错误引用主流程步骤。
- 三类共 27 个场景均缺少代码要求的步骤级 `inputs`，因此不能直接通过当前 `BusinessCaseSpec` 导入。
