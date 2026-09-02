# DeepSeek 与通义千问重试分析

## 结论

两个模型并不是模型 ID 不可用，也不是接口 Key 或 Prompt 基本错误。问题主要来自思考强度和长 JSON 输出：使用 `low` 时，DeepSeek 会把大量完成预算消耗在 `reasoning_content`，通义千问长请求容易在网关等待超时；改为 `reasoning_effort=none` 后，两个模型均成功生成 1 条候选并通过本地紧凑格式校验和展开后的官方加载校验。

## 实际重试

| 模型 | 批次 | 结果 | 候选编号 | 耗时 |
|---|---|---|---|---:|
| DeepSeek V4 Pro | `规范漂移-DeepSeek重试-第02批` | 成功 | `overtime-exception-001` | 约 105 秒 |
| 通义千问 Flash | `规范漂移-Qwen重试-第02批` | 成功 | `norm-drift-security-001` | 约 42 秒 |

两条候选分别位于：

```text
data/candidate_batches/archive/20260829_历史试点/规范漂移-DeepSeek重试-第02批/deepseek-v4-pro-0813/
data/candidate_batches/archive/20260829_历史试点/规范漂移-Qwen重试-第02批/qwen3.8-flash/
```

每个目录均有 `candidate_batch.json`、`expanded_cases.jsonl`、`request_raw.json` 和 `response_raw.json`。

## 诊断证据

用同一长 Prompt、`max_completion_tokens=1000` 做短输出诊断：DeepSeek 在约 27 秒返回 HTTP 200，但 `finish_reason=length`、正文为空、`reasoning_content` 约 1944 字符，说明思考内容挤占了输出预算；通义千问在 `low` 下 90 秒仍未返回。随后使用 `none` 和完整 16384 输出预算重试，两者均正常完成。

## 后续固定设置

`scripts/run_five_model_pilot.py` 已调整为：DeepSeek 和通义千问默认使用 `reasoning_effort=none`、单次超时 360 秒；GPT、Claude、智谱仍使用已验证的 `low`。后续批量生成应保留模型、思考强度、Seed、耗时和完整请求证据，不能把超时结果当成模型质量结论。

