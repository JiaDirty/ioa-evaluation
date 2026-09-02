# 收口交接：DSH 会话进行到的位置与下一步

> 本文件是 DSH 会话中断时的交接记录。Codex 接手后以本文件为唯一事实来源，不要假设任何旧结论。

## 一、总目标（未变）

把 `D:\个人文件\学习文件\实习\IOA测评搭建` 收口成正式项目：单一架构、唯一 CLI、统一数据布局、451 条数据（11 参考 + 440 候选）同一四层模型与同一 Registry、11 条真实编译 + 等价回归、440 条确定性修复/重写/拒绝、55 条正式 v1 数据、API 生成可断点恢复、全量测试通过、干净目录端到端重建。

## 二、已完成（本轮代码已落盘，尚未 git 提交）

### 1. 源码架构已收口为 9 模块（`src/evaluation/scenario_generation/`）
- `models.py`：四层模型 ScenarioTask（**轻量任务卡，不再要求完整 case**）/ ScenarioKernel / EffectSpec / CompiledCase + 作者格式后端模型 + Registry/质量/修复模型 + 哈希封存函数。
- `catalog.py`：11 分支 + 每分支配额（`data/catalog/evaluation_catalog.yaml`，含 branches 段）。唯一事实来源。
- `registry.py`：真实 PipelineRegistry（原子写 + 事件历史 + `invalidate_artifact`/`invalidate_downstream` 精准失效）。
- `artifact_store.py`：真实原子读写/哈希/校验。
- `generation.py`：PipelineAPI（结构化输出、指数退避重试、JSON 解析修复 `extract_json_object`、幂等请求缓存、修订 `revise_effect`、渲染修复 Prompt）。
- `compiler.py`：确定性编译（extract_scenario_kernel/extract_effect_spec/compile_kernel_effect + 作者格式编译后端整体迁入）。
- `validation.py`：六路径演算（path_validation 全文迁入）+ 结构校验 validate_kernel_structure/validate_effect_structure/validate_compiled_case。
- `evaluation.py`：离线运行 + 双审核校验（不同 reviewer ID + 哈希绑定）+ 发布装配 select_release_members。
- `orchestrator.py`：纯协调层，完整状态机 + process/resume/submit_*/compile/validate_paths/validate_runtime/review/human/freeze/reject。
- `__init__.py`：仅导出正式公共接口。

已删除旧实现：`blueprint.py / compact.py / authoring.py / pipeline.py / pipeline_models.py / pipeline_api.py / repair.py / path_validation.py / quality_records.py / state_machine.py`，以及 `src/evaluation/catalog.py`、`src/evaluation/business_protocol/cases.py / contract_migration.py / tool_result_diagnostics.py`、整个 `src/evaluation/inspect_adapter/`。所有 `..catalog` 引用已改指 `..scenario_generation.catalog`。

### 2. 新状态机（models.py 的 `_ALLOWED_TRANSITIONS`）
TASK_READY / KERNEL_DRAFT / KERNEL_NEEDS_REVISION / KERNEL_READY / EFFECT_DRAFT / EFFECT_NEEDS_REVISION / EFFECT_READY / COMPILED / PATH_VALID / RUNTIME_VALID / SEMANTIC_ACCEPTED / HUMAN_ACCEPTED / FROZEN / REJECTED / GENERATION_FAILED / VALIDATION_FAILED。

### 3. 唯一 CLI `scripts/run_pipeline.py`（子命令）
`import / process / resume / validate / evaluate / review / human / freeze / generate / status`，支持 `--all / --task / --branch / --dry-run / --json` 输出。已删除其余 29 个旧脚本，只保留：
- `scripts/run_pipeline.py`
- `scripts/migrations/build_tasks.py`（451 条任务卡构建，唯一导入路径）
- `scripts/migrations/reference_case_conversion.py`（`convert_reference_case` + 新增 `build_reference_kernel_effect`：从冻结向量推导真实行为 Oracle，产出 READY EffectSpec）
- `scripts/verification/run_reference_equivalence.py`（已适配新 registry.json 结构）
- `scripts/build_source_package.py`（**尚未改造，仍读旧 manifest，见下一步**）

### 4. 数据布局已整理（未提交）
- `data/catalog/evaluation_catalog.yaml`（新增 branches 段）
- `data/raw/reference_sources/*.jsonl`（git mv 自 data/scenarios，11 条已跟踪）
- `data/raw/reference_sources/legacy_reference_manifest.json`（git mv 自 data/legacy_reference_manifest.json）
- `data/raw/candidate_sources/批量生成-第01轮/`（440 条原始候选，文件系统移动，**仅本地、不提交**，.gitignore 已加 `data/raw/candidate_sources/`）
- 其余候选历史批次移到 `archive/本机历史记录/候选批次历史/`
- 已 git rm：`data/scenario_pipeline`（9240 文件）、`data/candidate_datasets / candidate_migrations / candidate_reviews`、`data/evaluation_catalog.yaml`、`artifacts/`（48 文件旧事务证据）
- `.gitignore` 已更新：新增 `data/raw/candidate_sources/`、`data/workspace/`；删除旧 candidate_batches 等条目

### 5. 已删除的旧测试
test_bulk_generation_script / test_scenario_blueprint / test_scenario_generation_models / test_generic_scoring_contract / test_business_protocol_cases / test_tool_result_diagnostics / test_merge_migrated_datasets / test_scenario_authoring / test_scenario_path_validation / test_scenario_pipeline_api / test_scenario_pipeline_models / test_pipeline_architecture / test_inspect_adapter / test_candidate_rotation_review / test_review_generic_dataset。

剩余测试：test_business_protocol_loader / test_business_protocol_prompt / test_candidate_review / test_evaluation_catalog / test_evaluation_dataset / test_model_client / test_tool_gateway / test_tool_schema_validation。**这些仍 import 已删除的 `business_protocol.cases`，会收集失败，待改写。**

## 三、当前卡点（Codex 接手先解决这个）

`scripts/run_pipeline.py import` 报 `KeyError: "no branch for category='TRA' subtype=None"`。
已定位：参考案例（med-auth-drift/vague）与候选的 TRA 类别 metadata 里没有 `sub_mechanism`，需从 case_id 推导 drift/vague。
**已写修复但尚未验证**：`scripts/migrations/build_tasks.py` 里新增了 `_derive_subtype()`，并在 `build_reference_tasks` 与 `build_candidate_tasks` 中调用。直接重跑 `python scripts/run_pipeline.py import` 验证。

## 四、下一步顺序（Codex 继续）

1. 验证 import 成功（451 任务 + 11 参考 kernel/effect 推导）→ 若 TRA 仍报错，继续修 `_derive_subtype`。
2. `python scripts/run_pipeline.py process --all`（11 参考应走到 RUNTIME_VALID 前；440 应到 EFFECT_NEEDS_REVISION 或 KERNEL_NEEDS_REVISION）。
3. 跑等价回归 `python scripts/verification/run_reference_equivalence.py`，确认 11/11。
4. 修复剩余测试的 import（改从 `data/raw/reference_sources` 经 loader 加载，替代 `cases.get_business_cases`），并新增回归测试（见下方清单）。
5. 440 条：`process --all` 会为每条生成 repair_plan 并转入 EFFECT_NEEDS_REVISION/KERNEL_NEEDS_REVISION；有 API 密钥时 `--allow-live-api` 走 `_repair_effect` 修订，无密钥则停在外层阻塞态（每类都带明确 reason，不再有"无理由 EFFECT_DRAFT"）。
6. 生成缺口：`python scripts/run_pipeline.py generate --branch <id> --count N --allow-live-api`。
7. 质量链：六路径/离线运行已接；双审核 `review`（两个不同 reviewer_id）、`human`、`freeze --release v1` 产出 `data/releases/v1/`。
8. 改造 `scripts/build_source_package.py`：改从 `data/workspace/registry.json` 生成证据，去掉旧 manifest 读取，排除缓存/临时结果，包内跑安装+迁移+测试。
9. 重写 README + 架构/数据格式/API生成/运行/质量门槛/迁移文档，删除互相矛盾或描述旧双入口的文档（`docs/history/` 可保留历史）。
10. 全量 pytest + compileall + git diff --check，干净临时目录端到端重建（import→process→validate→evaluate→freeze）。

## 五、新增回归测试清单（用户点名必须覆盖）

1 轻量任务卡无需完整 case 即可创建；2 11/440 走同一状态机；3 EFFECT_DRAFT→修订→EFFECT_READY→COMPILED；4 Kernel 修订精准失效；5 Effect 修订精准失效；6 resume 实际续跑；7 11 条无 compiled=case 绕行；8 每个 EffectSpec 有可执行 Oracle；9 六路径从 EffectSpec 真实演算；10 唯一评测入口同时接受 11/440 已编译案例；11 双审核 reviewer 不同；12 质量记录 ID/哈希不匹配拒绝写入；13 批处理部分失败不报全完成；14 451 条无丢失；15 11 分支配额来自唯一 Catalog；16 干净临时目录可重建；17 正式源码无旧入口引用；18 正式模块非转发壳。

## 六、关键约束（务必遵守）

- 原始 11 条（data/raw/reference_sources）与 440 条（data/raw/candidate_sources）**绝不覆盖/删除**。
- 密钥在 `config/agent_llm_config.yaml`（已 gitignore），api key 字段存在、provider=openai、base_url=aihubmix.com/v1；环境变量未设。
- 不创建第二套架构、不保留两套正式流程、不做转发壳模块。
- 每完成一个实质里程碑可简短汇报，但不要等确认，连续执行。
- 全部完成后按用户要求输出一份基于真实运行证据的收口报告（含目录/流程/数量/回归/删除清单/测试命令输出/唯一命令/git/外部阻塞项）。
