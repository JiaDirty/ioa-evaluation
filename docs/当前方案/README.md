# 当前方案唯一入口

## 一句话定位

当前主线是十项 Agent 模型行为安全测评，共10类、11个代表性业务协议用例。旧八项160条 JSONL 是保留的数据资产，不等于当前十项主线的完整数据集。

## 阅读顺序

1. `十项测评总览.md`
2. `数据集结构与生成规范.md`
3. `十项测评场景扩增生成Prompt.md`
4. `方案与代码实现对照.md`
5. `运行与判分指南.md`
6. `判分与有效性规范.md`
7. `有效运行结果索引.md`

## 唯一入口

| 对象 | 当前入口 |
|---|---|
| 十项定义 | `data/evaluation_catalog.yaml` |
| 十项用例 | `src/evaluation/business_protocol/cases.py` |
| 十项运行 | `scripts/run_ten_item_evaluation.py` |
| 场景扩增 | `十项测评场景扩增生成Prompt.md` |
| 工作区检查 | `scripts/audit_workspace.py` |
| 八项160条兼容数据 | `data/generated/IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl` |

文件名中仍含“八项”的方案和结果文档用于解释前八项设计及已有在线证据，不再承担十项总入口职责。旧 Prompt 已移至 `docs/历史归档/场景扩增Prompt演进/`。
