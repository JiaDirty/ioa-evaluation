# IoA 测评框架拓扑图

## 一、三层架构总览

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                        实验控制层 (Experiment Control Layer)                  │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │  Experiment   │  │   Attack     │  │  Topology    │  │   Metrics    │   │
│  │   Runner      │  │  Injector    │  │  Controller  │  │   Engine     │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                  │                  │           │
└─────────┼─────────────────┼──────────────────┼──────────────────┼───────────┘
          │                 │                  │                  │
          ▼                 ▼                  ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     IoA 基础设施层 (IoA Infrastructure Layer)                 │
│                                                                             │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   Global      │  │    Task      │  │   Protocol   │  │   Shared     │   │
│  │  Registry     │  │  Marketplace │  │     Hub      │  │  Knowledge   │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                  │                  │           │
│  ┌──────┴───────┐  ┌──────┴───────┐                                           │
│  │   Global      │  │   LLM        │                                           │
│  │  Audit Log    │  │   Client     │                                           │
│  └──────┬───────┘  └──────┬───────┘                                           │
│         │                 │                                                   │
└─────────┼─────────────────┼───────────────────────────────────────────────────┘
          │                 │
          ▼                 ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      领域子生态层 (Sub-IoA Layer)                             │
│                                                                             │
│  ┌─────────────────────────┐    ┌─────────────────────────┐                │
│  │    Finance-IoA           │    │   Healthcare-IoA         │                │
│  │  ┌─────┐  ┌──────────┐  │    │  ┌─────┐  ┌──────────┐  │                │
│  │  │  GW │──│ Registry │  │    │  │  GW │──│ Registry │  │                │
│  │  └──┬──┘  └──────────┘  │    │  └──┬──┘  └──────────┘  │                │
│  │     │    ┌──────────┐   │    │     │    ┌──────────┐   │                │
│  │     ├────│  Agents  │   │    │     ├────│  Agents  │   │                │
│  │     │    └──────────┘   │    │     │    └──────────┘   │                │
│  │     │    ┌──────────┐   │    │     │    ┌──────────┐   │                │
│  │     └────│   Tools  │   │    │     └────│   Tools  │   │                │
│  │          └──────────┘   │    │          └──────────┘   │                │
│  │     ┌──────────────┐   │    │     ┌──────────────┐   │                │
│  │     │ Local Audit  │   │    │     │ Local Audit  │   │                │
│  │     └──────────────┘   │    │     └──────────────┘   │                │
│  └─────────────────────────┘    └─────────────────────────┘                │
│                                                                             │
│  ┌─────────────────────────┐    ┌─────────────────────────┐                │
│  │    Travel-IoA            │    │    News-IoA              │                │
│  │  ┌─────┐  ┌──────────┐  │    │  ┌─────┐  ┌──────────┐  │                │
│  │  │  GW │──│ Registry │  │    │  │  GW │──│ Registry │  │                │
│  │  └──┬──┘  └──────────┘  │    │  └──┬──┘  └──────────┘  │                │
│  │     │    ┌──────────┐   │    │     │    ┌──────────┐   │                │
│  │     ├────│  Agents  │   │    │     ├────│  Agents  │   │                │
│  │     │    └──────────┘   │    │     │    └──────────┘   │                │
│  │     │    ┌──────────┐   │    │     │    ┌──────────┐   │                │
│  │     └────│   Tools  │   │    │     └────│   Tools  │   │                │
│  │          └──────────┘   │    │          └──────────┘   │                │
│  │     ┌──────────────┐   │    │     ┌──────────────┐   │                │
│  │     │ Local Audit  │   │    │     │ Local Audit  │   │                │
│  │     └──────────────┘   │    │     └──────────────┘   │                │
│  └─────────────────────────┘    └─────────────────────────┘                │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 二、Attack 模块交互关系（核心）

学长指出：Attack模块与所有模块都有交互。以下是Attack模块的攻击面映射：

```
                          ┌──────────────────────┐
                          │    Attack Injector    │
                          │   (攻击注入中心)       │
                          └──────────┬───────────┘
                                     │
            ┌────────────────────────┼────────────────────────┐
            │                        │                        │
            ▼                        ▼                        ▼
    ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
    │   Registry    │      │   Protocol    │      │   Agent       │
    │   攻击面:      │      │   攻击面:      │      │   攻击面:      │
    │ · 身份伪造     │      │ · 协议降级     │      │ · Prompt注入   │
    │ · 能力虚报     │      │ · 语义篡改     │      │ · 越狱攻击     │
    │ · Sybil攻击   │      │ · 字段错配     │      │ · 数据窃取     │
    │ · 声誉操纵     │      │ · 协商污染     │      │ · 能力伪装     │
    │ · 证书伪造     │      │               │      │ · 安全绕过     │
    └───────────────┘      └───────────────┘      └───────────────┘
            │                        │                        │
            ▼                        ▼                        ▼
    ┌───────────────┐      ┌───────────────┐      ┌───────────────┐
    │   Gateway     │      │  Marketplace  │      │  Knowledge    │
    │   攻击面:      │      │   攻击面:      │      │   攻击面:      │
    │ · 授权绕过     │      │ · 恶意任务     │      │ · 知识注入     │
    │ · 授权漂移     │      │ · 路由操纵     │      │ · 谣言扩散     │
    │ · 网关操纵     │      │ · 错误组队     │      │ · 规范漂移     │
    │ · 中心节点攻击 │      │ · 执行链失控   │      │ · 虚假共识     │
    └───────────────┘      └───────────────┘      └───────────────┘
            │                        │
            ▼                        ▼
    ┌───────────────┐      ┌───────────────┐
    │   Audit       │      │  Human Loop   │
    │   攻击面:      │      │   攻击面:      │
    │ · 审计规避     │      │ · 人类绕过     │
    │ · 日志篡改     │      │ · 讨论扭曲     │
    │ · 责任链断裂   │      │ · 能动性弱化   │
    │ · 来源不明     │      │ · 判断让渡     │
    └───────────────┘      └───────────────┘
```

---

## 三、模块间数据流

### 3.0 Agentic 默认任务执行流

```
用户自然语言 prompt
    │
    ▼
Task API / ExperimentRunner
    │
    ▼
TaskMarketplace.execute_task()
    │
    ▼
AgenticOrchestrator
    ├─ TaskSpecificationAgent: prompt -> TaskSpec
    ├─ AgenticOrchestrationPlanner: TaskSpec -> capability-level DAG
    ├─ PlanValidator: no Agent ID / no endpoint / no hop_chain
    ├─ Gateway.discover_and_select(): Registry semantic discovery + verification
    ├─ Gateway.dispatch_agentic_subtask(): protocol negotiation + AgentAction loop
    └─ SynthesisAgent: sourced answer + evidence map
    │
    ▼
TaskResult + ExecutionGraph + EventBus trace + EvaluationEvidenceBundle
```

The environment may define fixed Sub-IoAs, Agents, protocols, topology, and
attacks. The agentic task input does not fix target domains, Agent order, or
hop chains.

### 3.1 正常任务执行流

The fixed single-domain/cross-domain/multi-hop branches below are retained as
`scripted` compatibility paths for older mechanism probes. They are not the
default runtime path for prompt-only tasks.

```
用户发布任务
    │
    ▼
ExperimentRunner.run_scenario()
    │
    ▼
TaskMarketplace.execute_task(Task)
    │
    ├──→ [单域任务] ──→ Gateway.handle_task()
    │                        │
    │                        ├─→ Registry.discover() → 选择Agent
    │                        ├─→ ProtocolNegotiator.negotiate() → 协商协议
    │                        ├─→ ProtocolAdapter.send_message() → 转发任务
    │                        ├─→ Agent.run_task() → LLM执行
    │                        ├─→ 收集Artifact
    │                        ├─→ SecurityCheck → 安全检查
    │                        └─→ AuditLogger.log() → 记录审计
    │
    ├──→ [跨域任务] ──→ 主Gateway → relay_to_sub_ioa() → 辅Gateway
    │
    ├──→ [多跳任务] ──→ GW1 → GW2 → GW3 → ... (授权链)
    │
    └──→ [产物复用] ──→ 引用之前任务的Artifact作为输入
```

### 3.2 攻击注入流

```
AttackInjector.generate_*()
    │
    ├──→ [LLM生成攻击] ──→ LLM Client → 生成对抗性prompt
    │                          │
    │                          ▼
    │                    Agent.run_task(恶意prompt)
    │                          │
    │                          ▼
    │                    LLMJudge.judge_attack_success()
    │
    ├──→ [直接注入] ──→ Registry.inject_fake_agent()
    │              ──→ Artifact(safe=False)
    │              ──→ SharedKnowledgeBase.add_knowledge(虚假信息)
    │
    └──→ [结构性攻击] ──→ SemanticMismatchSimulator.inject_mismatch()
                    ──→ ProtocolNegotiator(弱协议)
```

### 3.3 审计双写流

```
Gateway._log_audit()
    │
    ├──→ Global AuditLogger → 全局审计日志
    │
    └──→ Local AuditLogger  → 本地审计日志（每Sub-IoA独立）

审计指标计算:
    AuditLogger.compute_metrics()
        │
        ├──→ chain_completeness (调用链完整率)
        ├──→ attribution_accuracy (归因准确率)
        └──→ source_coverage (来源覆盖率)
```

---

## 四、代码文件与模块对应关系

| 模块 | 代码文件 | 核心类/函数 |
|------|---------|------------|
| Experiment Runner | `src/experiment/runner.py` | `ExperimentRunner`, `IoAEnvironment` |
| Attack Injector | `src/attacks/attack_injector.py` | `AttackInjector`, `AttackResult` |
| Topology Controller | `src/experiment/runner.py` | `TopologyController` |
| Metrics Engine | `src/experiment/runner.py` | `MetricsEngine` |
| Global Registry | `src/registry/registry.py` | `Registry` |
| Task Marketplace | `src/marketplace/marketplace.py` | `TaskMarketplace` |
| Protocol Hub | `src/protocol/adapters.py` | `A2AAdapter`, `MCPAdapter`, `ProtocolNegotiator`, `SemanticMismatchSimulator` |
| Shared Knowledge | `src/core/shared_knowledge.py` | `SharedKnowledgeBase`, `KnowledgeEntry` |
| Global Audit | `src/audit/audit_logger.py` | `AuditLogger` |
| LLM Client | `src/llm/client.py` | `OpenAIClient` |
| LLM Config | `src/llm/config.py` | `AgentLLMConfig`, `JudgeLLMConfig` |
| LLM Judge | `src/attacks/llm_judge.py` | `LLMJudge`, `JudgeVerdict` |
| Gateway | `src/gateway/gateway.py` | `Gateway` |
| Agent | `src/agents/ioa_agent.py` | `IoAAgent`, `create_sub_ioa_agent()` |
| Data Models | `src/core/data_models.py` | 所有Pydantic模型和枚举 |
| 风险测试 | `risk_tests/` | 18个测试类 |
| 数据 | `data/` | JSON种子样例 |

---

## 五、精简建议（小而美原则）

基于学长"小而美"的要求，以下是精简建议：

### 可以简化的部分

1. **`core/data_models.py`** — 当前定义了15+个Pydantic模型，有些未被使用（如`AuthRequest`, `JudgeResult`）。精简为实际使用的模型。
2. **`protocol/adapters.py`** — 三个协议适配器的实现高度重复。可以合并为一个通用适配器+配置。
3. **`registry/registry.py`** — 注入方法（inject_*）可以移到测试辅助模块，保持Registry本身简洁。
4. **`marketplace/marketplace.py`** — 四种执行模式可以简化为两种（单域+跨域），多跳和产物复用作为跨域的特例。

### 必须保留的部分

1. **Attack模块** — 这是论文的核心创新点，不能简化
2. **Gateway的8步流程** — 这是IoA测评环境的核心设计
3. **双写审计** — 本地+全局审计是重要的架构特征
4. **SharedKnowledgeBase** — 共享知识库是IoA的独特组件
5. **18个风险测试** — 完整覆盖学长的6维度×18子风险

### 文件结构精简

```
IOA测评搭建/
├── config/                    # 配置（保留）
│   ├── agent_llm_config.yaml
│   └── judge_llm_config.yaml
├── data/                      # 数据（新增）
│   ├── seeds/
│   │   ├── seed_001_identity_spoofing.json
│   │   └── seed_002_ecosystem_consensus.json
│   └── schema.json
├── docs/                      # 文档（新增）
│   ├── risk_alignment.md
│   ├── data_schema.md
│   └── framework_topology.md
├── risk_tests/                # 风险测试（保留）
│   ├── base_test.py
│   ├── registry.py
│   └── ...18个测试
├── src/                       # 源码（精简）
│   ├── agents/
│   │   └── ioa_agent.py
│   ├── attacks/
│   │   ├── attack_injector.py
│   │   └── llm_judge.py
│   ├── audit/
│   │   └── audit_logger.py
│   ├── core/
│   │   ├── data_models.py     # 精简
│   │   └── shared_knowledge.py
│   ├── experiment/
│   │   └── runner.py          # 精简：合并TopologyController和MetricsEngine
│   ├── gateway/
│   │   └── gateway.py
│   ├── llm/
│   │   ├── client.py
│   │   └── config.py
│   ├── marketplace/
│   │   └── marketplace.py     # 精简
│   ├── protocol/
│   │   └── adapters.py        # 精简
│   └── registry/
│       └── registry.py        # 精简：移出inject_*方法
└── run_experiment.py           # 入口（保留）
```
