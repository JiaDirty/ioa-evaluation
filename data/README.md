# 当前数据目录

主目录当前只保留 `evaluation_catalog.yaml`，它是十项测评名称、顺序、代表用例和在线
状态的唯一机器可读清单。

通过模型生成的新场景不是正式数据。每个生成批次必须先通过：

1. 严格 JSON Schema 和 Pydantic 校验；
2. 正常条件与风险条件的单变量检查；
3. 工具参数、工具响应和状态变化一致性检查；
4. 恢复阶段对真实机制运行产物的继承检查；
5. 答案泄露与浅层重复检查；
6. 人工审核和离线协议运行。

候选批次校验命令：

```powershell
.\.venv\Scripts\python.exe scripts\validate_scenario_generation.py <批次文件.json>
```

旧数据已经移入 `archive/旧版八项数据路线/data/`，不参与当前运行或统计。
