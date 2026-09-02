# API 生成

生成分两步：Provider 先输出 `ScenarioKernelDraft`，本地校验并冻结 Kernel 后，再输出绑定 Kernel 哈希的 `EffectSpecDraft`。最终案例不由模型直接编写，而由本地编译器确定性展开。

`PipelineAPI` 提供可替换 Provider、Pydantic 结构化输出、超时、指数退避、重试、解析修复、定向修订、请求意图缓存和阶段证据。真实请求必须显式使用 `--allow-live-api`；测试使用 Mock Provider，不消耗实时额度。重复执行同一任务会读取匹配的请求缓存，失败任务保留在 Registry 中并可由 `resume` 继续。
