# IOA 项目交接摘要

包名：`IOA测评搭建_ChatGPT-Pro_2026-09-01_v2`

当前主线是把旧候选和未来新数据统一到：

```text
TaskCard → ScenarioKernel → EffectSpec → 本地编译
→ 六路径验证 → 运行检查 → 语义审核 → 人工终审 → 冻结入库
```

本包仅完成代码、数据结构和离线控制层快照，构建时没有调用 AI Hub Mix。
440 条候选已进入离线迁移管线，但不能把结构可解析当成业务质量通过；状态和失败原因见
`evidence/scenario_pipeline/pipeline_status.json`。

建议先阅读 `docs/两阶段场景生产流水线.md`、`docs/ScenarioKernel与EffectSpec规范.md`、
`docs/方案与代码实现状态.md`，再运行测试。项目虚拟环境不在包内。
