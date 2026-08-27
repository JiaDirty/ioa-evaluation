# 测评代码入口

本目录保留两条边界明确的测评链，不再把它们混称为同一套数据集。

| 目录 | 定位 | 类别 | 当前用途 |
|---|---|---:|---|
| `business_protocol/` | 当前十项行为安全测评主线 | 10 类、11 个代表性用例 | 协议验证、真实模型试跑、后续场景扩增 |
| `scenario_generation/` | 十项候选批次的严格 Pydantic 包装 | 10 类 | AIHubMix structured output 与离线验收 |
| `agent_model/` | 早期八项 v2 数据集运行器 | 8 类、160 条开发期已见用例 | 保留实验复现和历史对照 |

十项名称、顺序、用例编号和在线状态的唯一机器可读来源是
`data/evaluation_catalog.yaml`。运行十项主线使用：

```powershell
.\.venv\Scripts\python.exe scripts\run_ten_item_evaluation.py --validate-only
```

八项160条数据的作者格式和完整交换格式见 `data/README.md`。
