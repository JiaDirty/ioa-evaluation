"""Export auditable snapshots of the information visible to tested models."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping

from ...core.data_models import Task
from .models import CommonCase
from .prompt_policy import validate_visible_package


SNAPSHOT_FORMAT_VERSION = "agent-visible-prompt-snapshot-1"


def build_prompt_snapshot(
    task: Task,
    case: CommonCase,
    *,
    tool_gateway: Any | None = None,
) -> dict[str, Any]:
    """Build one snapshot from the same fields used by the live runtime.

    Evaluation metadata is retained only under ``audit``.  ``model_visible``
    contains the package that may be submitted to the tested model.
    """

    payload = task.payload if isinstance(task.payload, dict) else {}
    allowed_tool_ids = [str(item) for item in payload.get("allowed_tool_ids", [])]
    tool_descriptors = _visible_tool_descriptors(tool_gateway, allowed_tool_ids)
    model_visible = {
        "task_text": task.description,
        "public_state": payload.get("public_state", {}),
        "role_state": payload.get("role_state", {}),
        "upstream_artifacts": payload.get("upstream_artifacts", []),
        "turn_history": payload.get("turn_history", []),
        "tool_descriptors": tool_descriptors,
        "output_schema": payload.get("visible_action_schema", {}),
    }
    validate_visible_package(case, {
        key: model_visible[key]
        for key in (
            "task_text",
            "public_state",
            "role_state",
            "upstream_artifacts",
            "turn_history",
            "tool_descriptors",
        )
    })
    encoded = json.dumps(
        model_visible, ensure_ascii=False, sort_keys=True, default=str,
    ).encode("utf-8")
    return {
        "snapshot_format_version": SNAPSHOT_FORMAT_VERSION,
        "audit": {
            "case_id": case.case_id,
            "category_code": case.category_code,
            "variant": str(payload.get("variant", "")),
            "role_id": str(payload.get("role_id", "")),
            "round_index": int(payload.get("round_index", 0)),
            "run_id": str(payload.get("run_id", "")),
            "prompt_isolation_passed": True,
            "model_visible_sha256": hashlib.sha256(encoded).hexdigest(),
        },
        "model_visible": model_visible,
    }


def export_prompt_snapshots(
    snapshots: Iterable[dict[str, Any]],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Write lossless JSONL plus a compact Chinese audit index."""

    records = list(snapshots)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    jsonl_path = destination / "model_visible_prompt_snapshots.jsonl"
    index_path = destination / "model_visible_prompt_snapshot_index.md"
    manifest_path = destination / "model_visible_prompt_snapshot_manifest.json"

    jsonl_text = "".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True, default=str) + "\n"
        for item in records
    )
    jsonl_path.write_text(jsonl_text, encoding="utf-8")

    by_category = Counter(item["audit"]["category_code"] for item in records)
    by_variant = Counter(item["audit"]["variant"] for item in records)
    case_ids = sorted({item["audit"]["case_id"] for item in records})
    failed = [
        item for item in records
        if item["audit"].get("prompt_isolation_passed") is not True
    ]
    manifest = {
        "snapshot_format_version": SNAPSHOT_FORMAT_VERSION,
        "generation_mode": "maximal_deterministic_control_flow_without_model_calls",
        "record_count": len(records),
        "case_count": len(case_ids),
        "case_ids": case_ids,
        "records_by_category": dict(sorted(by_category.items())),
        "records_by_variant": dict(sorted(by_variant.items())),
        "prompt_isolation_failure_count": len(failed),
        "jsonl_sha256": hashlib.sha256(jsonl_text.encode("utf-8")).hexdigest(),
    }
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    lines = [
        "# 模型可见输入快照索引",
        "",
        "本目录由本地输入审计生成，不调用被测模型。JSONL 文件逐条保留真正允许模型看到的任务、公开状态、角色状态、上游产物、历史、工具说明和输出格式。",
        "",
        f"- 用例数：{len(case_ids)}",
        f"- 输入快照数：{len(records)}",
        f"- 输入隔离失败数：{len(failed)}",
        "- 生成方式：不调用模型，以占位输出推进所有预定角色和轮次",
        f"- JSONL 校验值：`{manifest['jsonl_sha256']}`",
        "",
        "## 分阶段数量",
        "",
    ]
    for variant, count in sorted(by_variant.items()):
        lines.append(f"- {variant}: {count}")
    lines.extend(["", "## 分测评数量", ""])
    for category, count in sorted(by_category.items()):
        lines.append(f"- {category}: {count}")
    lines.extend([
        "",
        "说明：真实运行中由模型主动发起的工具调用会产生后续模型输入，无法在调用模型前预知；这些后续输入由正式运行日志逐步记录。",
        "",
    ])
    index_path.write_text("\n".join(lines), encoding="utf-8")
    return {
        "record_count": len(records),
        "case_count": len(case_ids),
        "files": {
            "jsonl": str(jsonl_path),
            "index": str(index_path),
            "manifest": str(manifest_path),
        },
        "manifest": manifest,
    }


def _visible_tool_descriptors(
    tool_gateway: Any | None,
    allowed_tool_ids: list[str],
) -> list[dict[str, Any]]:
    if tool_gateway is None:
        return []
    descriptors: list[dict[str, Any]] = []
    registry = getattr(tool_gateway, "registry", None)
    for tool_id in allowed_tool_ids:
        descriptor = registry.get(tool_id) if registry is not None else None
        if descriptor is None:
            continue
        if hasattr(descriptor, "model_dump"):
            descriptors.append(descriptor.model_dump(mode="json"))
        elif isinstance(descriptor, Mapping):
            descriptors.append(dict(descriptor))
    return descriptors
