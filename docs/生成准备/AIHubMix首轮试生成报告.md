# AI Hub Mix 首轮试生成报告

日期：2026-08-28
目标类别：跨系统级联扩散
请求：每个候选模型各 1 条，并行试生成

| 模型 | HTTP | 完成原因 | 本地紧凑校验 | 主要结果 |
|---|---:|---|---|---|
| gpt-5.6-sol | 200 | stop | INVALID | 生成了较完整的业务链，但把 current_times、business_object、history_fixtures、key_node_fixture 等字段写成错误类型，并新增未定义字段 |
| deepseek-v4-pro-0813 | 200（首轮）/ 第二次连接断开 | stop（首轮） | INVALID | 首轮返回 3 条而非 1 条；使用 shared 与显式条件混合，违反紧凑格式 |
| claude-opus-5 | 200（首轮）/ 第二次连接断开 | stop（首轮） | INVALID | 首轮 `cases` 包含空对象；网关标记 `X-Structured-Output-Degraded: schema_keywords_stripped` |
| glm-5.3-flash | 连接断开 | — | 未得到结果 | 网关在 240 秒超时前断开连接 |

首轮结论：Prompt 的业务约束方向正确，但格式契约仍不够“机械化”。模型普遍会把紧凑格式误解为可以自由扩展字段，或把完整格式和紧凑格式混用；不能立即进入 220 条批量生成。已在待确认 Prompt v2 追加允许字段白名单、字段类型和紧凑格式反例。

本轮原始响应均保存在 `.local/candidates/aihubmix_pilot_20260828/`，正式 `data/scenarios` 未修改。
