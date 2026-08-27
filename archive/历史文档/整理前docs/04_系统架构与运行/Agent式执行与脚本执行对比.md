# Agent 式执行、离线执行与脚本执行的区别

本文解释项目中三种 `execution_mode` 的用途。代码中的英文取值是固定接口，不能翻译或改名。

## Agent 式执行 `agentic`

`execution_mode="agentic"` 是普通 API 任务的默认模式。

输入示例：

```json
{"prompt": "自然语言任务", "constraints": {}}
```

主要特点：

- 任务类型为动态任务；
- 调用方不指定目标域、所需能力和固定传递链；
- Planner 先生成能力节点，节点的 `assigned_agent_id` 初始为空；
- Gateway 在运行时发现并绑定具体 Agent；
- 工具调用、委托、人工输入和重新规划都使用结构化动作。

## 离线确定性执行 `offline_deterministic`

该模式使用与 Agent 式执行相同的状态流程、执行图、Gateway、Registry、Agent 循环和证据记录，但会用固定的本地响应代替真实模型接口。

它适合：

- 快速检查代码能否跑通；
- 在没有 API Key 时运行测试；
- 在持续集成环境中检查回归。

它不能用于判断真实模型的安全性，因为固定响应不代表真实模型行为。

## 脚本执行 `scripted`

该模式用于兼容旧的固定路线测试，可以使用以下旧字段：

- `task_type`；
- `origin_sub_ioa`；
- `target_sub_ioas`；
- `required_capabilities`；
- `payload.hop_chain`。

脚本模式必须明确标为旧版或固定路线模式，不能成为自然语言任务的默认执行方式。

## 判断方法

如果调用方事先知道 Agent 顺序和路线，就是脚本执行；如果调用方只提供目标和约束，由程序在运行时发现能力并绑定 Agent，就是 Agent 式执行。
