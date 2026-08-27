# IoA 十项 Agent 模型行为安全测评

本项目测量同一个大模型被实例化为不同业务角色 Agent 后，在记录读取、上下游交接、工具调用、状态改变和错误恢复中的行为安全。

## 十项测评

| # | 编码 | 名称 | 当前代表用例 |
|---:|---|---|---:|
| 1 | CAS | 跨系统级联扩散 | 1 |
| 2 | CON | 生态错误共识 | 1 |
| 3 | RUM | 谣言扩散 | 1 |
| 4 | NOR | 规范漂移 | 1 |
| 5 | INC | 激励错配 | 1 |
| 6 | JUD | 判断让渡 | 1 |
| 7 | DIS | 讨论扭曲 | 1 |
| 8 | AGE | 能动性弱化 | 1 |
| 9 | TRA | 信任与授权链 | 2 |
| 10 | CRE | 资格申报 | 1 |

名称、顺序、用例编号和在线状态的唯一机器可读来源是
[`data/evaluation_catalog.yaml`](data/evaluation_catalog.yaml)。当前十项主线共有10类、11个代表性用例。

## 唯一当前主线

```text
scripts/run_ten_item_evaluation.py
  -> src/evaluation/business_protocol/cases.py
  -> BusinessProtocolRunner
  -> baseline / mechanism / recovery
  -> key_node / full_chain
  -> 模型意图 / 工具执行 / 状态变化 / 最终影响
```

只做离线结构校验，不会调用模型：

```powershell
.\.venv\Scripts\python.exe scripts\run_ten_item_evaluation.py --validate-only
.\.venv\Scripts\python.exe scripts\audit_workspace.py
```

真实模型运行必须同时指定 `--execution-mode agentic-live` 和
`--allow-live-api`，避免误触发付费请求。

## 两套资产的边界

| 资产 | 范围 | 定位 |
|---|---:|---|
| `src/evaluation/business_protocol/` | 十类、11个代表性用例 | 当前研究和扩增主线 |
| `src/evaluation/agent_model/` | 八类、160条 | 保留的 v2 开发期数据与历史复现实验 |
| `risk_tests/` | 旧18项系统机制检查 | 平台能力检查，不与模型行为结果混合统计 |

160条 v2 数据全部为开发过程已见数据，`held_out=0`。它们可以用于协议回归和开发期基准，但不能写成未见数据泛化结果。TRA、CRE 已进入十项协议代码，目前没有加入这160条旧数据。

## 数据格式

- `data/agent_model_cases/`：紧凑作者格式，共享策略和分类常量只写一次；
- `data/generated/`：构建得到的完整 v2 JSONL，供交换和现有运行器使用，不手工编辑；
- `data/evaluation_catalog.yaml`：十项统一清单；
- `data/README.md`：格式、生成和校验规则。

重新生成并校验完整160条数据：

```powershell
.\.venv\Scripts\python.exe scripts\build_agent_model_dataset.py
.\.venv\Scripts\python.exe scripts\run_agent_model_suite.py --validate-only
```

## 阅读入口

1. [`docs/当前方案/README.md`](docs/当前方案/README.md)
2. [`docs/当前方案/十项测评总览.md`](docs/当前方案/十项测评总览.md)
3. [`docs/当前方案/数据集结构与生成规范.md`](docs/当前方案/数据集结构与生成规范.md)
4. [`docs/当前方案/十项测评场景扩增生成Prompt.md`](docs/当前方案/十项测评场景扩增生成Prompt.md)

旧方案、旧 Prompt 和旧运行记录统一保留在 `docs/历史归档/`，只用于追溯，不作为当前实现依据。

## 本机文件边界

`.venv/`、`.local/`、`evidence/`、运行数据库、密钥配置和真实运行原始结果均被 Git 忽略。本机运行结果默认写入 `.local/`，不要把模型密钥或原始敏感记录提交到仓库。
