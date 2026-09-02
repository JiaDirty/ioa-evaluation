# Inspect AI 适配层

当前项目保留自己的场景生成、编译、工具状态引擎和通用判分契约，只把批量实验调度、
模型接入、日志和通用统计交给 Inspect AI。适配不是重写测评，也不会改变正常条件、风险
条件和恢复条件的含义。

## 数据与运行边界

一条完整 IOA 场景对应一个 Inspect `Sample`。安全行为和危险行为是隐藏判分标准，不会
拆成六个任务，也不会进入被测模型消息。自定义 `Solver` 在一个样本内部执行：

```text
正常条件
  -> 风险条件
  -> 风险条件产生实际危险时才执行恢复条件
```

每个智能体步骤仍由 `BusinessProtocolRunner` 构造角色输入、工具和上游交接。工具仍由
`BusinessToolEnvironment` 执行并改变业务状态。适配器只将 Inspect 的异步模型调用转换
为现有运行器使用的模型回合格式。

## 判分

`ioa_protocol_scorer` 不调用裁判模型，而是读取现有确定性判分结果。每条样本记录：

- 正常、风险和恢复运行是否有效；
- 正常和风险条件下的意图、动作、实际影响是否安全；
- 是否触发恢复，以及恢复是否有效；
- 风险条件是否相对正常条件产生了新增危险影响。

无效响应、接口错误和无法判断的结果用空值表示，不会被记为安全。

## 离线检查

以下命令不调用外部模型：

```powershell
.\.venv\Scripts\python.exe scripts\run_inspect_evaluation.py --validate-only

.\.venv\Scripts\python.exe scripts\run_inspect_evaluation.py `
  --case-id travel-scope-001 `
  --execution-mode offline-scripted `
  --display none
```

默认读取 `data/scenarios/` 中哈希锁定的 11 条历史基准。运行新数据时显式指定：

```powershell
.\.venv\Scripts\python.exe scripts\run_inspect_evaluation.py `
  --validate-only `
  --dataset-profile generic_expandable `
  --data <新数据文件或目录>
```

## AI Hub Mix 真实运行

真实运行沿用 `config/agent_llm_config.yaml` 中的密钥、地址和模型配置，也可以使用
`--model` 临时覆盖模型名称。必须同时提供两个开关：

```powershell
.\.venv\Scripts\python.exe scripts\run_inspect_evaluation.py `
  --execution-mode agentic-live `
  --allow-live-api `
  --model gpt-5.6-sol `
  --max-connections 8
```

结果以 Inspect 原生 `.eval` 日志写入 `.local/results/inspect_ai/`。其中包含模型请求、
工具轨迹、IOA 配对结果、Token 用量、错误和分项得分。

## 旧数据说明

接入 Inspect 不会自动修复旧的 440 条候选。候选必须先补全场景语义和通用判分契约，
通过本地编译、严格校验和语义审核后，才能使用 `generic_expandable` 入口运行。
