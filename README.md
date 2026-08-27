# IoA 十项 Agent 模型行为安全测评

本项目评估大模型作为不同业务角色协作时，如何读取记录、传递上游产物、调用工具、
改变业务状态，以及在发现错误后完成恢复。

当前版本只保留一条主线：十个测评类别、十一个代表性业务场景。其中前八项已有真实
模型在线运行材料，信任与授权链、资格申报已完成代码和离线验证，尚未进行真实模型
在线测评。

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
data/         十项统一目录、当前 JSONL 场景和未来候选批次
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

## 阅读入口

1. [十项测评总览](docs/十项测评总览.md)
2. [方案与代码实现状态](docs/方案与代码实现状态.md)
3. [数据集与场景生成规范](docs/数据集与场景生成规范.md)
4. [十项测评场景生成 Prompt](docs/十项测评场景生成Prompt.md)
5. [运行与判分指南](docs/运行与判分指南.md)
6. [当前结果索引](docs/结果记录/当前结果索引.md)

本机密钥、模型原始响应和运行状态均不提交 Git。历史材料需要查阅时，从
[`archive/README.md`](archive/README.md) 进入。
