# 工作区清理差异摘要

基线提交：`ff3c34fbfed8310c60f47e45c9f424cba32129b3`

## 主目录

主目录只保留：`.venv/`、`archive/`、`config/`、`data/`、`docs/`、`scripts/`、
`src/`、`tests/`、`.git`、`.gitignore`、`pytest.ini`、`README.md` 和
`requirements.txt`。

## 归档

- `archive/旧版八项数据路线/`：旧 160 条数据、运行器、实现、测试和校准资料；
- `archive/旧版平台与十八项测评/`：旧前后端、平台、十八项系统测评和旧工具辅助层；
- `archive/历史文档/`：旧方案、旧 Prompt、旧架构、完整在线轨迹和调试记录；
- `archive/本机历史记录/`：约 6.48 GB 的本机结果、缓存和私有配置，受 Git 忽略；
- `archive/整理记录/`：本次差异、验证和恢复脚本。

暂存统计：532 个重命名、13 个新增、10 个删除；大量旧文件由 Git 识别为内容不变的
移动。`archive/本机历史记录/`、`node_modules/`、本机模型配置和环境文件均未暂存。

## 当前入口

- `README.md`：项目总入口；
- `docs/十项测评总览.md`：十项中文清单；
- `docs/十项测评场景生成Prompt.md`：唯一当前生成 Prompt；
- `docs/结果记录/十项测评情况说明.md`：唯一当前结果摘要；
- `data/evaluation_catalog.yaml`：唯一机器可读类别清单；
- `scripts/run_ten_item_evaluation.py`：唯一十项统一运行入口。
