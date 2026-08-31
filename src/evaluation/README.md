# 当前测评代码

本目录保留十个测评类别和十一个可运行的逻辑场景。之所以是十一个，是因为“信任与授权链”在数据层拆成了两个独立子机制；它们共享一个类别目录项，但分别作为可运行场景存在。

| 路径 | 作用 |
|---|---|
| `catalog.py` | 读取并校验十个类别的统一目录 |
| `business_protocol/loader.py` | 从 `data/scenarios/` 读取并校验 JSON/JSONL 场景 |
| `business_protocol/cases.py` | 原始手工场景构造器和回归基准 |
| `business_protocol/` | 十一个代表性逻辑场景、工具环境、运行器和判分逻辑 |
| `scenario_generation/` | 生成候选批次的严格结构与批次级质量门槛 |

统一入口为 `scripts/run_unified_evaluation.py`。例如，校验历史基准目录：

```powershell
.\.venv\Scripts\python.exe scripts\run_unified_evaluation.py --validate-only --data data\scenarios --dataset-profile legacy_reference --recursive
```

校验新增数据时使用 `--dataset-profile generic_expandable`。`run_ten_item_evaluation.py`
仍作为兼容别名保留，避免旧实验命令失效。退出主线的旧实现位于 `archive/`，不会被当前包导入。
