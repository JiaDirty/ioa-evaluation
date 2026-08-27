"""Reproducible run manifest construction without exposing credentials."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def file_hash(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def tree_hash(project_root: Path, paths: list[Path]) -> str:
    records: list[tuple[str, str]] = []
    for path in paths:
        children = [path] if path.is_file() else sorted(path.rglob("*.py"))
        for child in children:
            if child.is_file():
                records.append((str(child.relative_to(project_root)), file_hash(child)))
    return sha256_bytes(json.dumps(records, sort_keys=True).encode())


def build_manifest(
    project_root: Path,
    dataset: Path,
    suite_run_id: str,
    resolved_config: dict[str, Any],
    planned_order: list[str],
    split: dict[str, list[str]],
) -> dict[str, Any]:
    case_hashes: dict[str, str] = {}
    for line in dataset.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        case_hashes[str(record["case_id"])] = sha256_bytes(
            json.dumps(record, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
    commit = _git(project_root, ["rev-parse", "HEAD"])
    diff = _git(project_root, ["diff", "--binary", "HEAD"])
    config_hash = sha256_bytes(
        json.dumps(resolved_config, ensure_ascii=False, sort_keys=True).encode()
    )
    agent_root = project_root / "src" / "evaluation" / "agent_model"
    return {
        "suite_run_id": suite_run_id,
        "git_commit": commit.strip(),
        "dirty_diff_hash": sha256_bytes(diff.encode()),
        "dataset_hash": file_hash(dataset),
        "case_hashes": case_hashes,
        "code_hash": tree_hash(project_root, [agent_root, project_root / "scripts" / "run_agent_model_suite.py"]),
        "package_lock_hash": _package_lock_hash(project_root),
        "environment": {
            "python": sys.version,
            "platform": platform.platform(),
            "pydantic": importlib.metadata.version("pydantic"),
        },
        "model_config_hash": config_hash,
        "tool_manifest_hash": tree_hash(project_root, [agent_root / "tools"]),
        "prompt_hash": tree_hash(project_root, [agent_root / "context_builder.py", project_root / "src" / "runtime"]),
        "fixture_policy_hash": sha256_bytes(json.dumps(case_hashes, sort_keys=True).encode()),
        "topology_hash": sha256_bytes(json.dumps({"sub_ioas": ["finance", "healthcare", "travel", "news"], "topology": "full_mesh"}, sort_keys=True).encode()),
        "resolved_execution_config": resolved_config,
        "dataset_split": split,
        "dataset_split_hash": sha256_bytes(json.dumps(split, sort_keys=True).encode()),
        "dataset_provenance": {
            "dataset_role": "development_exposed_preregistered_evaluation",
            "all_cases_seen_during_evaluator_development": True,
            "held_out_case_count": len(split.get("held_out", [])),
            "formal_claim_restriction": (
                "Results are benchmark measurements on development-exposed "
                "synthetic cases, not held-out generalization evidence."
            ),
        },
        "planned_order": planned_order,
        "actual_order": [],
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "finished_at_utc": None,
        "formal_eligibility_rules_version": "agent-model-v2-strict-1",
        "seed_support": resolved_config.get("provider_seed"),
        "order_seed": resolved_config.get("order_seed"),
        "usage": {"input_tokens": 0, "output_tokens": 0, "cost": None},
        "runtime": {"latency_ms": [], "retry_count": 0},
    }


MANIFEST_FAMILY_FIELDS = (
    "dataset_hash", "code_hash", "package_lock_hash", "model_config_hash",
    "tool_manifest_hash", "prompt_hash", "fixture_policy_hash", "topology_hash",
    "dataset_split_hash", "formal_eligibility_rules_version", "formal_plan_hash",
    "tested_model_identity", "judge_model_identity",
)


def manifest_family_hash(manifest: dict[str, Any]) -> str:
    family = {key: manifest.get(key) for key in MANIFEST_FAMILY_FIELDS}
    return sha256_bytes(json.dumps(family, sort_keys=True, default=str).encode())


def assert_manifests_compatible(manifests: list[dict[str, Any]]) -> None:
    families = {manifest_family_hash(manifest) for manifest in manifests}
    if len(families) > 1:
        raise ValueError("results from incompatible manifest families cannot be merged")


def _git(project_root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args], cwd=project_root, capture_output=True, text=True,
        encoding="utf-8", errors="replace", check=False,
    )
    return result.stdout if result.returncode == 0 else ""


def _package_lock_hash(project_root: Path) -> str:
    paths = [
        path for name in ("requirements.txt", "pyproject.toml", "uv.lock", "poetry.lock")
        if (path := project_root / name).exists()
    ]
    return sha256_bytes("".join(file_hash(path) for path in paths).encode())
