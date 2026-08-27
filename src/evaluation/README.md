# 当前测评代码

本目录只保留十项主线：

| 路径 | 作用 |
|---|---|
| `catalog.py` | 读取并校验十项统一目录 |
| `business_protocol/loader.py` | 从 `data/scenarios/` 读取并校验 JSON/JSONL 场景 |
| `business_protocol/cases.py` | 原始手工场景构造器和回归基准 |
| `business_protocol/` | 十一个代表性业务场景、工具环境、运行器和判分逻辑 |
| `scenario_generation/` | 生成候选批次的严格结构与批次级质量门槛 |

离线入口为 `scripts/run_ten_item_evaluation.py --validate-only`。退出主线的旧实现位于
`archive/`，不会被当前包导入。
