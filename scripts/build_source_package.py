"""Build a shareable, credential-free source snapshot for the IOA project.

The package intentionally contains candidate JSON/JSONL inputs but omits raw
provider requests and responses.  Pipeline evidence is reduced to a portable
status snapshot so machine-specific absolute paths do not leak into the
archive or make the snapshot appear runnable on another computer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "IOA测评搭建_ChatGPT-Pro_2026-09-01_v4"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_tree(source: Path, target: Path, *, skip_names: set[str] | None = None) -> int:
    skip_names = skip_names or set()
    excluded_directories = {"__pycache__", ".pytest_cache", ".git", ".venv", ".local"}
    copied = 0
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in excluded_directories for part in relative.parts):
            continue
        if not path.is_file() or path.name in skip_names or path.suffix in {".pyc", ".pyo"}:
            continue
        _copy_file(path, target / relative)
        copied += 1
    return copied


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _portable_pipeline_snapshot(pipeline_root: Path) -> dict[str, Any]:
    registry = _load_json(pipeline_root / "registry" / "registry_manifest.json")
    validation = _load_json(pipeline_root / "registry" / "registry_validation.json")
    repair = _load_json(pipeline_root / "repair_summary.json")
    manifest = _load_json(pipeline_root / "pipeline_manifest.json")

    effect_status_counts: dict[str, int] = {}
    quality_status_counts: dict[str, int] = {}
    for entry in manifest.get("entries", []):
        effect_status = str(entry.get("effect_status", "UNKNOWN"))
        quality_status = str(entry.get("quality_status", "PENDING"))
        effect_status_counts[effect_status] = effect_status_counts.get(effect_status, 0) + 1
        quality_status_counts[quality_status] = quality_status_counts.get(quality_status, 0) + 1

    return {
        "schema_version": "source_package_pipeline_snapshot_v1",
        "canonical_entrypoint": "scripts/run_pipeline.py",
        "canonical_schema": [
            "ScenarioTask",
            "ScenarioKernel",
            "EffectSpec",
            "CompiledCase",
        ],
        "canonical_registry": "registry.json",
        "pipeline_version": manifest.get("pipeline_version"),
        "source_kind": manifest.get("entries", [{}])[0].get("source_kind", "legacy"),
        "source_root": "data/candidate_batches/批量生成-第01轮",
        "pipeline_root": "data/scenario_pipeline/场景生产流水线-第01轮",
        "candidate_count": validation.get("candidate_count", registry.get("candidate_count")),
        "task_card_count": validation.get("task_card_count"),
        "event_count": validation.get("event_count"),
        "all_candidates_processed": validation.get("all_candidates_processed"),
        "source_hashes_verified": validation.get("source_hashes_verified"),
        "source_hash_mismatch_count": validation.get("source_hash_mismatch_count"),
        "absolute_path_count": validation.get("absolute_path_count"),
        "missing_artifact_count": validation.get("missing_artifact_count"),
        "registry_status": validation.get("status"),
        "stage_status_counts": registry.get("stage_counts", {}),
        "quality_status_counts": quality_status_counts,
        "effect_status_counts": effect_status_counts,
        "evaluation_item_counts": registry.get("evaluation_item_counts", {}),
        "repair_result_status_counts": repair.get("result_status_counts", {}),
        "live_api_calls": repair.get("live_api_calls", 0),
        "portable_evidence": True,
        "note": (
            "This is a portable status snapshot. Full pipeline artifacts remain in the "
            "working tree because their references include machine-specific paths."
        ),
    }


def _iter_package_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path


def build_package(output_root: Path, package_name: str) -> tuple[Path, Path, dict[str, Any]]:
    package_dir = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    if package_dir.exists() or zip_path.exists():
        raise FileExistsError(f"refusing to overwrite existing package: {package_dir}")

    package_dir.mkdir(parents=True)
    for directory in ("src", "scripts", "tests", "docs"):
        _copy_tree(ROOT / directory, package_dir / directory)

    for relative in (".gitignore", "pytest.ini", "requirements.txt", "README.md"):
        _copy_file(ROOT / relative, package_dir / relative)
    for relative in (
        "data/evaluation_catalog.yaml",
        "data/legacy_reference_manifest.json",
    ):
        _copy_file(ROOT / relative, package_dir / relative)
    _copy_tree(ROOT / "data" / "scenarios", package_dir / "data" / "scenarios")

    # Config examples and model profiles are useful; local credential files are not.
    for path in sorted((ROOT / "config").glob("*.yaml")):
        if path.name in {"agent_llm_config.yaml", "judge_llm_config.yaml"}:
            continue
        _copy_file(path, package_dir / "config" / path.name)

    # Keep candidate inputs needed by the tests and external reviewers, but not
    # provider request/response transcripts.
    _copy_tree(
        ROOT / "data" / "candidate_batches" / "批量生成-第01轮",
        package_dir / "data" / "candidate_batches" / "批量生成-第01轮",
        skip_names={"request_raw.json", "response_raw.json"},
    )

    pipeline_root = ROOT / "data" / "scenario_pipeline" / "场景生产流水线-第01轮"
    evidence_root = package_dir / "evidence" / "scenario_pipeline"
    evidence_root.mkdir(parents=True, exist_ok=True)
    snapshot = _portable_pipeline_snapshot(pipeline_root)
    (evidence_root / "pipeline_status.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    for relative in (
        "registry/registry_manifest.json",
        "registry/registry_validation.json",
        "repair_summary.json",
    ):
        _copy_file(pipeline_root / relative, evidence_root / Path(relative).name)

    status = _run_git("status", "--short", "--branch")
    diff = _run_git("diff", "--", "README.md", "src", "scripts", "tests", "docs")
    (package_dir / "evidence" / "working_tree_status.txt").parent.mkdir(
        parents=True, exist_ok=True
    )
    (package_dir / "evidence" / "working_tree_status.txt").write_text(
        status + "\n", encoding="utf-8"
    )
    (package_dir / "evidence" / "working_tree_diff.patch").write_text(
        diff, encoding="utf-8"
    )

    manifest: dict[str, Any] = {
        "schema_version": "source_package_manifest_v3",
        "package_name": package_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_root": str(ROOT),
        "git_head": _run_git("rev-parse", "HEAD"),
        "git_status": status.splitlines(),
        "live_api_called_during_build": False,
        "excluded": [
            ".git",
            ".venv",
            ".local",
            ".pytest_cache",
            "archive",
            "config/agent_llm_config.yaml",
            "config/judge_llm_config.yaml",
            "data/candidate_batches/**/request_raw.json",
            "data/candidate_batches/**/response_raw.json",
            "full data/scenario_pipeline artifacts with machine-specific paths",
        ],
        "files": [],
    }
    for path in _iter_package_files(package_dir):
        relative = path.relative_to(package_dir).as_posix()
        manifest["files"].append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}
        )
    manifest["file_count"] = len(manifest["files"])
    (package_dir / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    scope = f"""# IOA 测评搭建源代码包

这是 {package_name} 的可移植源码快照，目标是让外部模型或协作者审阅当前统一数据模型、单一 Registry、PipelineOrchestrator，以及端到端生成流程的迁移状态。

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
"""
    (package_dir / "PACKAGE_SCOPE.md").write_text(scope, encoding="utf-8")

    handoff = f"""# IOA 项目交接摘要

包名：`{package_name}`

当前目标是把 11 条历史基准、440 条候选和未来新数据统一到唯一规范化案例格式：

```text
ScenarioTask → ScenarioKernel → EffectSpec → CompiledCase
→ 六路径验证 → 运行检查 → 语义审核 → 人工终审 → 冻结入库
```

本包仅完成代码、数据结构和离线控制层快照，构建时没有调用 AI Hub Mix。
440 条候选已进入离线迁移管线，但不能把结构可解析当成业务质量通过；状态和失败原因见
`evidence/scenario_pipeline/pipeline_status.json`。

当前代码仅为历史 11 条保留回归适配器；新的生产主线已经统一到 `ScenarioTask`、
`registry.json` 和 `PipelineOrchestrator`，具体迁移规则和验收门禁见
`docs/统一数据格式与端到端生成流程.md`。

建议先阅读 `docs/两阶段场景生产流水线.md`、`docs/ScenarioKernel与EffectSpec规范.md`、
`docs/方案与代码实现状态.md`，再运行测试。项目虚拟环境不在包内。
"""
    (package_dir / "CHATGPT_PRO_HANDOFF.md").write_text(handoff, encoding="utf-8")

    # Write the final manifest again so the generated metadata files are listed.
    manifest["files"] = []
    for path in _iter_package_files(package_dir):
        relative = path.relative_to(package_dir).as_posix()
        if relative == "PACKAGE_MANIFEST.json":
            continue
        manifest["files"].append(
            {"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)}
        )
    manifest["file_count"] = len(manifest["files"])
    (package_dir / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in _iter_package_files(package_dir):
            archive.write(path, f"{package_name}/{path.relative_to(package_dir).as_posix()}")
    return package_dir, zip_path, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "packages")
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()
    package_dir, zip_path, manifest = build_package(args.output_root, args.name)
    print(f"PACKAGE_DIR={package_dir}")
    print(f"ZIP_PATH={zip_path}")
    print(f"FILE_COUNT={manifest['file_count']}")
    print(f"ZIP_SHA256={_sha256(zip_path)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
