# 数据目录

## 目录职责

| 路径 | 格式 | 是否手工编辑 | 用途 |
|---|---|---:|---|
| `evaluation_catalog.yaml` | YAML | 是 | 十项名称、顺序、用例和状态的唯一清单 |
| `agent_model_cases/_shared_defaults.json` | JSON | 通过脚本生成 | 八项 v2 共享默认值 |
| `agent_model_cases/*.jsonl` | 紧凑 JSONL | 是 | 八项160条作者格式，只保留每条差异 |
| `generated/*.jsonl` | 完整 JSONL | 否 | 构建后的兼容交换格式和运行输入 |
| `calibration/` | JSON/JSONL | 是 | Judge 校准资产 |
| `seeds/` | JSON | 历史资产 | 旧18项系统机制种子 |

## 为什么使用两层格式

原八项160条数据在每条记录里重复模型参数、执行策略、可见性策略、上下文策略、结果层级和分类常量。紧凑作者格式把这些内容移到 `_shared_defaults.json`，`CaseLoader` 在 Pydantic 校验前恢复成完整 v2 对象。

这样同时满足：

1. 人工编辑时只关注本场景真正变化的字段；
2. 现有运行器仍接收完整 v2 对象；
3. 对外交换文件仍然单行自包含；
4. 共享策略修改只需要改一处并重新构建。

## 构建和校验

从完整交换文件重新生成紧凑作者文件：

```powershell
.\.venv\Scripts\python.exe scripts\compact_agent_model_dataset.py
```

从紧凑作者文件构建完整交换文件：

```powershell
.\.venv\Scripts\python.exe scripts\build_agent_model_dataset.py
```

验证160条数据和整个工作区：

```powershell
.\.venv\Scripts\python.exe scripts\run_agent_model_suite.py --validate-only
.\.venv\Scripts\python.exe scripts\audit_workspace.py
```

AIHubMix 生成的十项候选批次使用
`src/evaluation/scenario_generation/models.py` 校验，不与八项 v2 JSONL 混写。

## 边界

`agent_model_cases/` 只有 CAS、CON、RUM、NOR、INC、JUD、DIS、AGE 八类，而且160条全部属于开发已见数据。当前十项主线中的 TRA、CRE 在 `src/evaluation/business_protocol/` 中实现，不能通过给旧 JSONL 补两个分类文件就宣称已经形成十项正式数据集。
