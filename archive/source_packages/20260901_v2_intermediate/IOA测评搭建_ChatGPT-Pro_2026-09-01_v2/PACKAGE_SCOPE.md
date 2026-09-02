# IOA 测评搭建源代码包

这是 IOA测评搭建_ChatGPT-Pro_2026-09-01_v2 的可移植源码快照，目标是让外部模型或协作者审阅当前两阶段场景生产实现。

包含：`src/`、`scripts/`、`tests/`、`docs/`、配置示例、11 个可运行场景、第一轮 440 条候选的
`candidate_batch.json` 与 `expanded_cases.jsonl`，以及离线场景管线的状态摘要和 registry 证据。

不包含 Git 历史、虚拟环境、缓存、本机配置密钥、候选原始 API 请求/响应和带机器绝对路径的完整
管线中间目录。原始候选和完整管线仍保留在源工作区，不会被本包替代。

包内运行：

```powershell
python -m pytest -q
python -m compileall -q src scripts tests
```

本包构建过程未调用任何实时模型 API。
