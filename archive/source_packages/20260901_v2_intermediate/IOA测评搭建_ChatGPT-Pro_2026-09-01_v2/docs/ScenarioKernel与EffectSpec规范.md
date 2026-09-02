# ScenarioKernel 与 EffectSpec 规范（v1）

这两个文件是“作者层”中间稿，不是给被测智能体看的输入，也不是最终运行文件。

## ScenarioKernel：场景设计稿

`ScenarioKernel` 只回答业务问题：

- 这条数据属于哪个测评项、业务领域和业务对象；
- 有哪些角色，各自负责什么；
- 主流程和恢复流程有哪些步骤、怎样交接；
- 正常条件与风险条件共享哪些事实，唯一改变的因果变量是什么；
- 两种条件下模型能看到哪些记录、历史和上游产物；
- 预期的安全行为、危险行为、风险后果和恢复目标是什么。

它不让模型直接编写复杂的条件映射、运行时编号或 `generic_scoring_v1` 规则。`kernel_id`、来源信息和内容哈希由本地程序生成，避免模型伪造身份或篡改已经审核过的场景语义。

主步骤的输入条件名是 `normal`、`risk`，恢复步骤只使用 `recovery`。恢复步骤用 `recovery_source_step_ids` 指明它要处置的主流程风险来源；`upstream_step_ids` 只表示恢复流程内部依赖。

## EffectSpec：执行效果说明

`EffectSpec` 只回答“工具怎样产生可观察效果”：

- 工具名称、说明、参数 JSON Schema 和客观返回；
- 固定状态变化，或从真实参数/返回复制到状态的绑定；
- 每个关键步骤的安全/危险行为模式；
- 三种条件下的安全终态和危险终态规则；
- 与 ScenarioKernel 的 `kernel_id`、`kernel_sha256` 绑定。

工具效果必须来自参数或真实返回。不能因为工具名字叫“approve”就自动推断它一定危险，也不能把实验条件写死到返回值里替模型做决定。

## 两种状态

- `DRAFT`：从旧数据提取或模型刚生成的草案，允许保留待修问题，绝不进入正式运行。
- `READY_FOR_COMPILE`：已经补齐行为和终态契约，并通过本地严格校验；随后还要经过六条标准路径、真实运行、模型审核和人工审核。

本地编译器把这两个中间稿转换成完整 `BusinessCaseSpec`，自动生成条件映射、恢复来源元数据和三层通用判分契约。编译器不会改变 ScenarioKernel 的标题、目的、因果变量或风险后果。

## 哈希与版本

当前版本号为 `scenario_kernel_v1`、`effect_spec_v1`。每个文件都保存内容 SHA-256；修改内核后，旧效果规格的绑定立即失效，必须重新生成或审核。未知版本不会被静默当作当前版本读取，必须提供显式升级函数。
旧候选重复提取时，`source.extracted_at` 使用源文件的稳定修改时间，因此同一源文件的
语义内容哈希不会因断点续跑时间不同而漂移；该时间仍保留在来源记录中，供审计追溯。

## 文件状态与最终入库

中间产物按候选 UID 分文件保存，来源和每次调用证据由 manifest 关联：

```text
raw/
kernels/
kernel_reviews/
effect_specs/
compiled/
validations/
runtime_checks/
semantic_reviews/
accepted/
quarantine/
pipeline_manifest.json
```

格式校验通过只说明字段可解析；统一 registry 中，质量状态与发布归属分开记录，只有完成
人工终审并明确加入某个 release 的候选才能进入正式数据集。旧版 runner 的
`FORMAL_ACCEPTED` 仍作为兼容状态保留；旧候选即使进入 `REWRITE_REQUIRED` 或
`QUARANTINED`，原始文件仍保持不变。
