# 运行说明

```powershell
.\.venv\Scripts\python.exe scripts\run_pipeline.py import
.\.venv\Scripts\python.exe scripts\run_pipeline.py process --all
.\.venv\Scripts\python.exe scripts\run_pipeline.py resume --all
.\.venv\Scripts\python.exe scripts\run_pipeline.py validate --all
.\.venv\Scripts\python.exe scripts\run_pipeline.py evaluate --all
.\.venv\Scripts\python.exe scripts\run_pipeline.py review --task <task-id> --reviews <reviews.json>
.\.venv\Scripts\python.exe scripts\run_pipeline.py human --task <task-id> --decision ACCEPT --reviewer <id> --reason <reason>
.\.venv\Scripts\python.exe scripts\run_pipeline.py freeze --release v1
.\.venv\Scripts\python.exe scripts\run_pipeline.py status
```

所有命令使用 `data/workspace/registry.json`。`process` 和 `validate` 只有在没有失败、草稿或待修订任务时才报告完成；`--dry-run` 只读取当前状态，不写入产物。
