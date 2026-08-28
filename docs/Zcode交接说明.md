# 项目交接说明

本文档用于将项目当前状态交接给 Zcode。内容以当前工作区和最近一次验证结果为准。

## 1. 用户与当前目标

用户是王航舟，负责智能体安全评测中的 IOA 方向。当前目标是：在已有基准场景的基础上，使用 AI Hub Mix 中的多个模型小批量生成候选数据，比较生成质量，选择主力模型，再批量扩充并经过本地校验和人工审核。

用户偏好直接、清晰的中文解释。面向用户说明测评时使用完整中文名称，不使用三字母内部类别简称；代码、文件名和现有数据字段中的内部标识可以保留。

## 2. 当前数据事实

- `data/scenarios` 下当前有 11 个正式 `.jsonl` 文件。
- 每个 `.jsonl` 文件当前只有 1 条完整基准场景，因此当前正式基准数据总数是 11 条，不是 160 条。
- 最初的 8 项测评各有 1 条已运行过的基准数据；后来新增的 3 个测评场景也各有 1 条基准数据。
- 会议中的 160 条是扩充目标：原 8 项各扩充到 20 条，即 8 x 20 = 160。若 11 项全部扩充到 20 条，目标总数应为 220 条，其中新增 3 项需要 60 条。
- 当前尚未调用 AI Hub Mix 生成正式候选批次，也没有把生成数据混入正式目录。

当前 11 个正式文件对应的中文测评名称为：

1. 跨系统级联扩散
2. 生态错误共识
3. 谣言扩散
4. 规范漂移
5. 激励错配
6. 判断让渡
7. 讨论扭曲
8. 能动性弱化
9. 授权转述漂移
10. 含糊授权越界
11. 资格申报

## 3. 数据与代码的关系

- `src/evaluation/business_protocol/cases.py`：Python 内置参考实现，用于测试、导出和部分兼容场景；不是当前批量数据的唯一存储位置。
- `data/scenarios/*.jsonl`：当前正式运行入口使用的外置场景数据。每个文件的一行是一个完整 JSON 对象，包含完整场景内容。
- `scripts/export_builtin_cases.py`：从内置 Python 实现重新导出外置 JSONL。修改 `cases.py` 后需要重新导出并校验两者一致。
- `docs/十项测评场景生成Prompt.md`：当前唯一的场景扩增 Prompt，版本标记为 v4。虽然文件名写“十项”，当前目录实际有 11 个场景文件，生成时应以 `data/scenarios` 和测评目录为准。
- `scripts/validate_scenario_generation.py`：校验 AI 生成的候选批次结构。
- `scripts/run_ten_item_evaluation.py`：验证当前正式场景协议，不代表只有 10 条数据。
- `scripts/audit_workspace.py`：检查目录、目录清单、正式数据和归档状态。

## 4. 一条完整场景包含什么

外层对象包含 `schema_version` 和 `case`。`case` 内包含：唯一编号、所属测评类别、标题、测评目的、主流程 `steps`、恢复流程 `recovery_steps`、每种条件下的初始状态 `initial_state`，以及场景级 `metadata`。

每个步骤包含步骤编号、上游步骤编号、角色编号、角色职责、任务编号、任务内容、当前时间、条件专用时间、业务对象、三种条件下的输入、原始用户消息、历史上下文、工具列表、评测参考行为、关键节点标记和步骤级元数据。

每个条件下的输入由两部分组成：原始业务记录 `records` 和上游交接结果 `upstream_artifacts`。业务记录包含编号、类型、产生时间、正文和来源引用；交接结果包含编号、产生角色、产生时间、正文和证据引用。

每个工具包含名称、描述、参数 JSON Schema、三种条件下的模拟返回、三种条件下的状态变化、可用条件和风险等级。`reference` 中的安全行为、危险行为和客观危险工具列表只供判分器使用，不应直接展示给被测模型。

三种条件分别是：`baseline` 正常条件，`mechanism` 注入目标风险的条件，`recovery` 加入更正或恢复信息的条件。

## 5. 已完成的质量审查与修复

已逐条检查 11 条基准场景的结构、步骤依赖、时间可见性、条件差异、工具参数、危险动作标记、状态变化、恢复流程和场景目标。

已修复两处明确问题：

1. “规范漂移”中任务对象曾出现 `OXY-2`，采购记录却使用 `MON-7`，且设备和数量描述不一致；现在统一为 `MON-7` 的供氧及监护设备备用电池 2 个。
2. “讨论扭曲”的首个参与账号没有点赞工具，却把点赞工具标记为该步骤的客观危险动作；现在首个参与账号的危险工具列表为空，后续参与账号仍保留点赞工具。

## 6. 最近验证结果

必须优先使用项目自己的 `.venv`，不要依赖 Anaconda：

```powershell
cd "D:\个人文件\学习文件\实习\IOA测评搭建"
.\.venv\Scripts\Activate.ps1
```

当前项目解释器：

```text
D:\个人文件\学习文件\实习\IOA测评搭建\.venv\Scripts\python.exe
Python 3.13.14
```

最近一次验证：

```text
.venv\Scripts\python.exe scripts\export_builtin_cases.py
status=EXPORTED; file_count=11

.venv\Scripts\python.exe scripts\run_ten_item_evaluation.py --validate-only
status=VALID; case_count=11; step_count=57; planned_result_count=22; provider_calls=0

.venv\Scripts\python.exe scripts\audit_workspace.py
status=VALID; failed_checks=[]; protocol_case_count=11; protocol_step_count=57

.venv\Scripts\python.exe -m pytest -q
122 passed, 3 subtests passed

git diff --check
exit status 0; no whitespace errors
```

## 7. 当前 Git 状态

远端仓库为私有仓库 `JiaDirty/ioa-evaluation`，当前分支 `main`，本地与 `origin/main` 保持同步。

关键提交：

```text
4cfd8d2 refactor: load business scenarios from external JSONL
0c775c0 fix: align norm-drift purchase object and discussion unsafe-tool marking
```

`0c775c0` 就是上述质量修复提交，包含 `data/scenarios/04_规范漂移.jsonl`、`data/scenarios/07_讨论扭曲.jsonl`、`src/evaluation/business_protocol/cases.py` 和 `tests/test_business_protocol_cases.py`，并新增两个回归测试。

历史分支 `codex/a2a-compliance-rigorous-evals`、`codex/agentic-decision-agents` 和 `master` 已确认完全并入 main 后删除；远端跟踪引用已清理，当前只保留 `main` 一个分支。

## 8. 接下来建议的执行顺序

1. 先阅读 11 个正式 JSONL 和 `docs/十项测评场景生成Prompt.md`，不要重新设计已经冻结的基础结构。
2. 每个测评类别分别调用多个 AI Hub Mix 模型，每批只生成少量候选，用于比较质量；建议记录模型、请求参数、批次编号、Seed、原始请求和原始响应。
3. 先做本地结构校验，再做场景目标、因果隔离、业务合理性、时间一致性、工具可执行性、重复度和模型可见信息检查。
4. 比较不同模型的候选质量和人工淘汰率，选择主力生成模型；不要只按价格选择。
5. 用主力模型批量生成候选数据，生成结果放在候选目录，不要直接覆盖 `data/scenarios` 正式基准文件。
6. 通过人工审核后，再把合格数据纳入正式数据集；记录生成数量、保留数量、淘汰数量和淘汰原因。
7. 每次修改正式数据后重新运行导出、校验、审计和完整测试，并检查 Git diff。

当前最重要的未完成事项是：AI Hub Mix 多模型小批量生成试验。尚未进行真实模型调用，也没有确定最终主力模型。

## 9. 交接时的边界

- 不要把 11 条基准数据说成已经有 160 条。
- 不要把 `cases.py` 和外置 JSONL 说成两套不同的正式数据；前者是内置参考实现，后者是当前外置运行数据。
- 不要把结构校验通过等同于人工审核完成。
- 不要把模型运行结果写回场景定义；运行结果应单独保存。
- API 额度由课题组提供且充足，可以放心调用；但仍应记录每次调用的模型和用量，小批量试验先行，避免无效调用。
