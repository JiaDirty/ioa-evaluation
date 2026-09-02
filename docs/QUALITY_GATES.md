# 质量门槛

正式案例必须通过 Schema、引用完整性、唯一 ID、Kernel 因果一致性、EffectSpec 工具与状态变化、行为 Oracle、六路径演算和离线运行。语义审核记录必须绑定任务 ID、Kernel 哈希、Effect 哈希、CompiledCase 哈希和运行器版本，并由两个不同 reviewer ID 产生；人工决定后才能冻结发布。

六路径包括正常安全、直接危险、条件性危险、工具失败、恢复成功和恢复失败。任何阶段失败都要在 Registry 记录原因，不得用完成状态掩盖。
