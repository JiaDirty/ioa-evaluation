# 当前数据目录

`evaluation_catalog.yaml` 是十项测评名称、顺序、代表用例和在线状态的机器可读清单。
正式回归场景位于 `data/scenarios/`：当前按 11 个可运行场景分成 11 个 JSONL 文件，
每行是一条完整场景。它们的规范化哈希登记在 `reference_source_manifest.json`，只用于
复现旧实验。新增数据不写进 Python 场景工厂，也不冒用这 11 个编号。

当前结构：

```text
data/
├─ evaluation_catalog.yaml
├─ reference_source_manifest.json
└─ scenarios/
   ├─ 01_跨系统级联扩散.jsonl
   ├─ 02_生态错误共识.jsonl
   ├─ ...
   └─ 11_资格申报.jsonl
```

每条记录可以是直接的 `BusinessCaseSpec` JSON 对象，也可以使用
`{"schema_version":"business_case_spec_v1","case":{...}}` 包装。加载器会先解析和
校验，再交给现有运行器；新增场景还必须包含 `generic_scoring_v1` 通用判分契约。
生成批次的 Prompt、模型、Seed 和审核信息仍应保存在独立的
候选批次文件中，不混入正式场景文件。

通过模型生成的新场景不是正式数据。每个生成批次必须先通过：

1. 严格 JSON Schema 和 Pydantic 校验；
2. 正常条件与风险条件的单变量检查；
3. 工具参数、工具响应和状态变化一致性检查；
4. 恢复阶段对真实机制运行产物的继承检查；
5. 答案泄露与浅层重复检查；
6. 通用判分契约完整且与场景工具、状态字段一致；
7. 人工审核和离线协议运行。

候选批次校验命令：

```powershell
.\.venv\Scripts\python.exe scripts\validate_scenario_generation.py <批次文件.json>
.\.venv\Scripts\python.exe scripts\run_evaluation_dataset.py --validate-only --data <展开后目录>
```

旧数据已经移入 `archive/旧版八项数据路线/data/`，不参与当前运行或统计。
