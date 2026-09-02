# 来源与迁移

`data/raw/reference_sources/` 保存 11 条只读参考来源，`data/raw/candidate_sources/批量生成-第01轮/` 保存 440 条只读候选来源。两者都通过 `scripts/migrations/build_tasks.py` 转换成相同的 `ScenarioTask`，来源差异只记录在 provenance 和 lineage。

一次性参考转换位于 `scripts/migrations/reference_case_conversion.py`，11 条等价回归位于 `scripts/verification/run_reference_equivalence.py`。这些工具只处理来源迁移和历史证据，不构成第二条正式生产流程。
