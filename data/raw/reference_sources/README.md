# 当前场景数据

本目录是当前评测运行的正式场景入口。每个 `.jsonl` 文件对应一个可运行场景，文件中
每一行是一条完整的场景记录。当前目录由 11 个基准场景组成，后续可以在对应文件中
追加经过质量审核的新变体。

## 记录格式

推荐使用带版本号的包装格式：

```json
{"schema_version":"business_case_spec_v1","case":{"case_id":"...","category":"...","title":"...","purpose":"...","steps":[],"recovery_steps":[],"initial_state":{},"metadata":{}}}
```

也兼容直接写 `BusinessCaseSpec` 对象的 JSON。加载器会读取包装中的 `case`，使用
Pydantic 校验必填字段、工具参数结构和条件枚举，然后交给现有运行器。

`step.reference`、`step.metadata` 和部分状态夹具属于评测器内部参考信息。提示词构造器
必须继续过滤这些字段，不能把安全参考答案或判分约束泄露给被测模型。

## 约束

- 所有 `case_id` 必须在整个目录内唯一；
- 一个文件可以有多行，一个场景的新增变体追加到该场景文件；
- 原始生成响应、Prompt、模型、Seed、人工审核结论放在候选批次目录，不写入本目录；
- 修改数据后先运行 `scripts/run_ten_item_evaluation.py --validate-only` 和测试套件。
