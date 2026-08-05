# Agent 式执行架构

本文说明普通 Agent 式任务在系统中的执行顺序、模型负责的内容、程序强制执行的边界，以及运行记录保存的位置。

## 默认执行流程

```text
POST /api/tasks {prompt, constraints}
  -> TaskSpecificationAgent（整理任务要求）
  -> AgenticOrchestrationPlanner（生成能力级计划）
  -> PlanValidator（检查计划）
  -> AgenticOrchestrator（调度计划）
  -> Gateway.discover_and_select（发现并选择 Agent）
  -> Gateway.dispatch_agentic_subtask（执行子任务）
  -> SynthesisAgent（汇总结果）
  -> TaskResult + ExecutionGraph + EvidenceBundle
```

最后三个对象分别表示任务结果、实际执行图和可追溯证据包。

## 模型与程序的职责边界

模型驱动或确定性实现的决策 Agent 可以提出：

- 任务要求；
- 以能力为单位的执行计划；
- `AgentAction` 动作，包括最终回答、工具调用、委托、询问用户、重新规划和失败；
- 多个结果的汇总决定。

以下事项由确定性程序判断，模型不能自行绕过：

- 身份和证书是否有效；
- 能力发现以及候选 Agent 的范围；
- 通信协议协商；
- 权限范围；
- 委托权限只能保持或缩小，不能自行扩大；
- ToolGateway 的参数结构和权限检查；
- 必须由人工确认的检查点。

## 计划格式要求

计划节点只描述所需能力、依赖关系、预期输出和输入绑定。计划中不能预先写死具体 Agent 编号、直接接口地址、目标 Sub-IoA 路径或 `hop_chain`。具体 Agent 只能在 Gateway 完成发现和验证后绑定。

## 运行循环

`Gateway.dispatch_agentic_subtask()` 在限定轮数内调用被选中的 Agent：

1. Agent 请求工具时，必须经过 `ToolGateway`；
2. 工具结果写入 `turn_history`；
3. 同一个 Agent 在下一轮收到该结果，再决定继续调用工具还是给出最终回答；
4. Agent 请求委托时，必须经过 `DelegationController`；
5. 委托被拒绝时按失败关闭；委托被允许时，程序向执行图增加节点并记录计划修改。

## 运行记录

EventBus 和产物元数据会记录：

- 任务要求；
- 初始执行图；
- 实际绑定的 Agent 和所属域；
- 候选 Agent 的发现与选择；
- 协议判断；
- Agent 动作；
- 工具调用；
- 人工确认；
- 计划修改；
- 最终证据对应关系。

测评场景还会在 `results/.../*.json` 中保存 `EvaluationEvidenceBundle`，用于事后检查模型输入、模型输出、工具实际执行结果和最终影响。
