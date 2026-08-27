# 当前数据目录

`evaluation_catalog.yaml` 是十项测评名称、顺序、代表用例和在线状态的机器可读清单。
正式场景数据位于 `data/scenarios/`：当前按 11 个可运行场景分成 11 个 JSONL 文件，
每行是一条完整场景。评测入口默认读取这些文件，不再把新增数据写进 Python 场景工厂。

当前结构：

```text
data/
├─ evaluation_catalog.yaml
└─ scenarios/
   ├─ 01_跨系统级联扩散.jsonl
   ├─ 02_生态错误共识.jsonl
   ├─ ...
   └─ 11_资格申报.jsonl
```

每条记录可以是直接的 `BusinessCaseSpec` JSON 对象，也可以使用
`{"schema_version":"business_case_spec_v1","case":{...}}` 包装。加载器会先解析和
校验，再交给现有运行器；生成批次的 Prompt、模型、Seed 和审核信息仍应保存在独立的
候选批次文件中，不混入正式场景文件。

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
