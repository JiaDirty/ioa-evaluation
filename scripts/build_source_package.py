#!/usr/bin/env python
"""Build a credential-free, reproducible source package from the formal layout."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NAME = "IOA_scenario_pipeline_20260902"
EXCLUDED_PARTS = {".git", ".venv", ".local", "__pycache__", ".pytest_cache", "packages"}
EXCLUDED_NAMES = {"request_raw.json", "response_raw.json", "agent_llm_config.yaml", "judge_llm_config.yaml"}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _copy_tree(source: Path, target: Path) -> int:
    copied = 0
    if not source.exists():
        return copied
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if any(part in EXCLUDED_PARTS for part in relative.parts):
            continue
        if not path.is_file() or path.name in EXCLUDED_NAMES or path.suffix in {".pyc", ".pyo"}:
            continue
        _copy_file(path, target / relative)
        copied += 1
    return copied


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=ROOT, check=False, capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    return result.stdout.strip()


def _registry_snapshot() -> dict[str, Any]:
    path = ROOT / "data/workspace/registry.json"
    if not path.is_file():
        return {"schema_version": "registry_snapshot_v1", "entry_count": 0, "stage_counts": {}}
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries", {})
    stage_counts: dict[str, int] = {}
    branch_counts: dict[str, int] = {}
    for entry in entries.values():
        stage = str(entry.get("stage", "UNKNOWN"))
        branch = str(entry.get("branch_id", "UNKNOWN"))
        stage_counts[stage] = stage_counts.get(stage, 0) + 1
        branch_counts[branch] = branch_counts.get(branch, 0) + 1
    return {
        "schema_version": "registry_snapshot_v1",
        "entry_count": len(entries),
        "stage_counts": dict(sorted(stage_counts.items())),
        "branch_counts": dict(sorted(branch_counts.items())),
        "event_count": len(payload.get("events", [])),
        "registry_schema_version": payload.get("schema_version"),
    }


def _package_files(package_dir: Path):
    for path in sorted(package_dir.rglob("*")):
        if path.is_file():
            yield path


def build_package(output_root: Path, package_name: str) -> tuple[Path, Path, dict[str, Any]]:
    package_dir = output_root / package_name
    zip_path = output_root / f"{package_name}.zip"
    if package_dir.exists() or zip_path.exists():
        raise FileExistsError(f"package already exists: {package_dir}")
    package_dir.mkdir(parents=True)

    for directory in ("src", "scripts", "tests", "docs"):
        _copy_tree(ROOT / directory, package_dir / directory)
    for relative in (".gitignore", "pytest.ini", "requirements.txt", "README.md"):
        source = ROOT / relative
        if source.is_file():
            _copy_file(source, package_dir / relative)
    _copy_tree(ROOT / "config", package_dir / "config")
    _copy_tree(ROOT / "data/catalog", package_dir / "data/catalog")
    _copy_tree(ROOT / "data/raw/reference_sources", package_dir / "data/raw/reference_sources")
    _copy_tree(ROOT / "data/raw/candidate_sources", package_dir / "data/raw/candidate_sources")
    _copy_tree(ROOT / "data/workspace", package_dir / "data/workspace")

    evidence = package_dir / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    snapshot = _registry_snapshot()
    (evidence / "registry_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (evidence / "git_status.txt").write_text(_git("status", "--short", "--branch") + "\n", encoding="utf-8")
    (evidence / "git_head.txt").write_text(_git("rev-parse", "HEAD") + "\n", encoding="utf-8")

    scope = """# IOA 场景生产与评测源码包

正式数据流程为：

```text
ScenarioTask -> ScenarioKernel -> EffectSpec -> CompiledCase
-> 六路径验证 -> 离线运行 -> 语义审核 -> 人工决定 -> 冻结发布
```

包内只有一个正式入口 `scripts/run_pipeline.py`，数据状态由
`data/workspace/registry.json` 记录。原始来源保存在 `data/raw/`，密钥、缓存和
模型请求响应不进入包。

在包根目录执行：

```powershell
python -m compileall -q src scripts tests
python -m pytest -q
python scripts/run_pipeline.py status
```
"""
    (package_dir / "PACKAGE_SCOPE.md").write_text(scope, encoding="utf-8")

    manifest: dict[str, Any] = {
        "schema_version": "source_package_manifest_v4",
        "package_name": package_name,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_head": _git("rev-parse", "HEAD"),
        "entrypoint": "scripts/run_pipeline.py",
        "api_called_during_build": False,
        "excluded": sorted(EXCLUDED_PARTS | EXCLUDED_NAMES),
        "files": [],
    }
    for path in _package_files(package_dir):
        relative = path.relative_to(package_dir).as_posix()
        manifest["files"].append({"path": relative, "bytes": path.stat().st_size, "sha256": _sha256(path)})
    manifest["file_count"] = len(manifest["files"])
    (package_dir / "PACKAGE_MANIFEST.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in _package_files(package_dir):
            archive.write(path, f"{package_name}/{path.relative_to(package_dir).as_posix()}")
    manifest["zip_sha256"] = _sha256(zip_path)
    return package_dir, zip_path, manifest


def verify_package(package_dir: Path, zip_path: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    listed = {item["path"]: item["sha256"] for item in manifest["files"]}
    actual = {
        path.relative_to(package_dir).as_posix(): _sha256(path)
        for path in _package_files(package_dir)
        if path.name != "PACKAGE_MANIFEST.json"
    }
    mismatches = sorted(path for path, digest in listed.items() if path != "PACKAGE_MANIFEST.json" and actual.get(path) != digest)
    with zipfile.ZipFile(zip_path) as archive:
        bad = archive.testzip()
    return {"file_hash_mismatches": mismatches, "zip_test": "PASS" if bad is None else bad}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=ROOT / "packages")
    parser.add_argument("--name", default=DEFAULT_NAME)
    args = parser.parse_args()
    package_dir, zip_path, manifest = build_package(args.output_root, args.name)
    verification = verify_package(package_dir, zip_path, manifest)
    print(json.dumps({
        "package_dir": str(package_dir),
        "zip_path": str(zip_path),
        "file_count": manifest["file_count"],
        "zip_sha256": manifest["zip_sha256"],
        **verification,
    }, ensure_ascii=False, indent=2))
    return 0 if not verification["file_hash_mismatches"] and verification["zip_test"] == "PASS" else 3


if __name__ == "__main__":
    raise SystemExit(main())
