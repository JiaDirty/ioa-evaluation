# 工作区清理后的主线

分支：`codex/agentic-decision-agents`

本次把根目录收敛为十项测评主线。当前代码只保留业务协议、候选场景生成、模型客户端
和实际使用的工具网关；旧 160 条数据路线、旧平台、十八项系统测评、旧测试、旧文档和
历史运行结果统一进入 `archive/`。

关键字段变更：

- 删除目录清单中的 `legacy_dataset_cases`，旧 160 条数据不再作为当前类别属性；
- 候选生成的 `target_category` 与场景 `category` 对外使用中文测评名称，加载时转换为
  程序内部值；
- 结构化响应 Schema 名称由旧路线专用名称改为中性 `structured_response`；
- `pytest.ini` 限制测试只发现当前 `tests/`，不会执行归档测试。

主线文档不再展示三个字母的测评简称。归档内容不参与当前导入、测试、运行或统计。

四份整理记录：

1. `archive/整理记录/工作区清理_20260827/MODIFIED_FILE.md`
2. `archive/整理记录/工作区清理_20260827/DIFF_FILE.md`
3. `archive/整理记录/工作区清理_20260827/VERIFICATION.txt`
4. `archive/整理记录/工作区清理_20260827/ROLLBACK.sh`
