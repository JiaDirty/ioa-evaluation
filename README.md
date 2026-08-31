# IoA 十项 Agent 模型行为安全测评

本项目评估大模型作为不同业务角色协作时，如何读取记录、传递上游产物、调用工具、
改变业务状态，以及在发现错误后完成恢复。

当前版本只保留一条主线：十个测评类别、十一个代表性业务场景。其中前八项已有真实
模型在线运行材料，信任与授权链、资格申报已完成代码和离线验证，尚未进行真实模型
在线测评。

第一轮候选数据生成已完成：8 个生成模型分别为 11 个场景生成 5 条，共 440 条。
候选数据位于 `data/candidate_batches/批量生成-第01轮/`，目前均已通过结构展开和
运行层加载校验，但没有通用判分契约，也尚未经过最终业务质量审核，因此不能进入正式
实验。首轮质量试审还发现跨批次编号重复、因果对照缺失、未来信息和工具定义冲突等问题。
它们作为历史候选保留，不会自动落入原 11 条的固定判分规则。

当前代码已经提供统一评测入口。以后新增场景必须携带声明式通用判分契约，运行器据此
读取场景自己的工具、状态绑定和判分规则，不再要求为每条数据增加 Python 分支。原 11
条则作为哈希锁定的回归基准保留，继续复现此前实验口径；两类数据可以通过同一个入口
混合加载和运行。

新数据不再要求生成模型直接手写完整运行结构。模型输出简洁作者格式，本地编译器自动
展开工具条件、状态绑定及意图/动作规则，并演算正常、风险和恢复的六条典型路径。完整
运行数据继续保留科研所需信息，但机械复杂度不再由模型承担。

## 十项测评

1. 跨系统级联扩散
2. 生态错误共识
3. 谣言扩散
4. 规范漂移
5. 激励错配
6. 判断让渡
7. 讨论扭曲
8. 能动性弱化
9. 信任与授权链
10. 资格申报

机器可读的名称、顺序、用例编号和状态统一保存在
[`data/evaluation_catalog.yaml`](data/evaluation_catalog.yaml)。

## 当前目录

```text
config/       真实模型接口配置示例
data/         十项统一目录、回归基准清单、当前场景和候选批次
docs/         当前方案、生成 Prompt、运行指南和有效结果
scripts/      当前运行、审计和候选数据校验入口
src/          十项业务协议、场景生成 Schema、模型客户端和工具网关
tests/        只覆盖当前主线的测试
archive/      退出主线的旧代码、旧数据、旧文档和历史结果
pytest.ini    限制测试只发现当前主线
```

`archive/` 不参与当前导入、测试、运行或结果统计。

## 离线验证

以下命令不会调用模型：

```powershell
.\.venv\Scripts\python.exe scripts\audit_workspace.py
.\.venv\Scripts\python.exe scripts\run_ten_item_evaluation.py --validate-only
.\.venv\Scripts\python.exe scripts\run_evaluation_dataset.py --validate-only --data <新数据目录>
.\.venv\Scripts\python.exe scripts\run_unified_evaluation.py --validate-only --data data\scenarios
.\.venv\Scripts\python.exe -m pytest -q
```

## 真实模型运行

先导出并人工检查模型可见输入：

```powershell
.\.venv\Scripts\python.exe scripts\run_ten_item_evaluation.py `
  --validate-only `
  --export-prompts .local\prompt_review\ten_item_prompts.jsonl
```

真实调用必须同时显式指定 `--execution-mode agentic-live` 和 `--allow-live-api`。
没有第二个开关时，程序不会产生供应商调用。

当前场景数据默认从 `data/scenarios/` 读取。每个 JSONL 文件对应一个可运行场景，
每行是一条独立记录；`cases.py` 只保留原始手工场景构造器，作为回归基准，不再是
正常评测的数据入口。

项目同时提供 Inspect AI 标准实验入口。它保持现有 IOA 场景、工具状态和确定性判分
不变，将模型接入、并发、重试、日志和通用统计交给 Inspect 管理。一条完整场景对应
一个 Inspect 样本，正常、风险和必要时的恢复运行仍作为一个配对实验执行：

```powershell
.\.venv\Scripts\python.exe scripts\run_inspect_evaluation.py --validate-only
```

完整边界和真实模型命令见 [Inspect AI 适配层](docs/Inspect%20AI适配层.md)。

以后新增数据使用 `scripts/run_unified_evaluation.py`。该入口允许任意数量的 JSON、
JSONL 文件或目录；历史基准由兼容适配器运行，新增数据必须有 `generic_scoring_v1` 通用
判分契约。旧候选的迁移工具是 `scripts/migrate_candidate_contracts.py`。完整说明见
[可扩充评测数据架构](docs/可扩充评测数据架构.md)。

## 阅读入口

1. [十项测评总览](docs/十项测评总览.md)
2. [方案与代码实现状态](docs/方案与代码实现状态.md)
3. [数据集与场景生成规范](docs/数据集与场景生成规范.md)
4. [当前场景生成 Prompt](docs/十项测评场景生成Prompt_作者版v3.md)
5. [运行与判分指南](docs/运行与判分指南.md)
6. [可扩充评测数据架构](docs/可扩充评测数据架构.md)
7. [当前结果索引](docs/结果记录/当前结果索引.md)
8. [候选数据质量试审](docs/结果记录/候选数据质量试审-第01轮.md)
9. [数据复杂度与架构深度评估](docs/架构深度评估.md)
10. [Inspect AI 适配层](docs/Inspect%20AI适配层.md)

本机密钥、模型原始响应和运行状态均不提交 Git。历史材料需要查阅时，从
[`archive/README.md`](archive/README.md) 进入。
