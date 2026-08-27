"""Export the authoritative SQLite trace into readable research artifacts."""

from __future__ import annotations

import ast
import hashlib
import html
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


_SECRET_KEYS = {
    "api_key", "apikey", "authorization", "password", "secret",
    "access_token", "refresh_token", "cookie", "set-cookie",
    "headers", "extra_headers", "default_headers", "http_headers",
}
_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+|sk-)[a-z0-9._-]{12,}")
_PROMPT_SECTION_PATTERN = re.compile(r"(?m)^## [^\r\n]+(?:\r?\n|$)")

_VARIANT_LABELS = {
    "baseline": "基准过程",
    "risk": "风险过程",
    "recovery": "恢复过程",
}

_VARIANT_ORDER = {"baseline": 0, "risk": 1, "recovery": 2}

_CATEGORY_INFO = (
    ("CAS", "级联传播"),
    ("CON", "虚假共识"),
    ("RUM", "谣言传播"),
    ("NOR", "规范漂移"),
    ("INC", "激励错配"),
    ("JUD", "判断权让渡"),
    ("DIS", "讨论操纵"),
    ("AGE", "用户自主性侵蚀"),
)

_PROCESS_DIR_NAME = "八项测评流程记录"

_READABLE_EFFECT_EVENTS = {
    "artifact",
    "message_forward",
    "board_action",
    "memory",
    "user_state",
    "gateway_decision",
    "recovery",
    "reward",
    "business_action",
}


def export_execution_trace(
    db_path: Path,
    output_dir: Path,
    *,
    suite_run_id: str,
    run_outcome: dict[str, Any] | None = None,
) -> dict[str, Any]:
    records, standalone_events, run_results, scenario_snapshots = _load_records(
        db_path
    )
    summary = _build_summary(
        suite_run_id,
        records,
        standalone_events,
        run_results,
        scenario_snapshots,
    )
    if run_outcome is not None:
        summary["run_outcome"] = dict(run_outcome)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "execution_trace.jsonl"
    markdown_path = output_dir / "execution_trace.md"
    html_path = output_dir / "execution_trace.html"
    summary_path = output_dir / "trace_summary.json"

    jsonl_records = [
        {"record_type": "summary", **summary},
        *records,
        *run_results,
        *scenario_snapshots,
        *standalone_events,
    ]
    jsonl_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, default=str) + "\n"
            for record in jsonl_records
        ),
        encoding="utf-8",
    )
    markdown_path.write_text(
        _render_markdown(
            summary,
            records,
            standalone_events,
            run_results,
            scenario_snapshots,
        ), encoding="utf-8"
    )
    html_path.write_text(
        _render_html(
            summary,
            records,
            standalone_events,
            run_results,
            scenario_snapshots,
        ),
        encoding="utf-8",
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    process_dir = output_dir / _PROCESS_DIR_NAME
    process_dir.mkdir(parents=True, exist_ok=True)
    process_paths: dict[str, Path] = {}
    for index, (category_code, category_name) in enumerate(_CATEGORY_INFO, 1):
        path = process_dir / (
            f"{index:02d}_{category_code}_{category_name}_流程记录.md"
        )
        category_records = [
            record for record in records
            if _category_code(record.get("case_id")) == category_code
        ]
        category_results = [
            result for result in run_results
            if _category_code(result.get("case_id")) == category_code
        ]
        category_events = [
            event for event in standalone_events
            if _category_code(event.get("case_id")) == category_code
        ]
        category_snapshots = [
            snapshot for snapshot in scenario_snapshots
            if _category_code(snapshot.get("case_id")) == category_code
        ]
        path.write_text(
            _render_category_markdown(
                summary,
                category_code,
                category_name,
                category_records,
                category_results,
                category_events,
                category_snapshots,
            ),
            encoding="utf-8",
        )
        process_paths[category_code] = path

    files = [
        jsonl_path,
        markdown_path,
        html_path,
        summary_path,
        *process_paths.values(),
    ]
    complete_paths = [jsonl_path, markdown_path, html_path, summary_path]
    complete_record_files = [
        path.relative_to(output_dir).as_posix() for path in complete_paths
    ]
    process_record_files = {
        code: path.relative_to(output_dir).as_posix()
        for code, path in process_paths.items()
    }
    return {
        "record_count": len(records),
        "run_result_count": len(run_results),
        "scenario_snapshot_count": len(scenario_snapshots),
        "standalone_event_count": len(standalone_events),
        "model_call_count": summary["model_call_count"],
        "provider_request_count": summary["provider_request_count"],
        "files": {
            path.relative_to(output_dir).as_posix(): {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
            for path in files
        },
        "complete_record_files": complete_record_files,
        "process_record_files": process_record_files,
        # Compatibility name retained for existing callers.  These are the
        # eight concise process records, not the complete forensic record.
        "readable_category_files": process_record_files,
        "usage": summary["usage"],
        "runtime": summary["runtime"],
    }


def _load_records(
    db_path: Path,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    connection = sqlite3.connect(str(db_path))
    connection.row_factory = sqlite3.Row
    try:
        events_by_step: dict[tuple[str, str, int], list[dict[str, Any]]] = {}
        all_events: list[dict[str, Any]] = []
        for row in connection.execute(
            "SELECT * FROM evaluation_events ORDER BY run_id, sequence, timestamp"
        ):
            item = dict(row)
            item["payload"] = _loads(item.pop("payload_json", "{}"), {})
            item["parent_event_ids"] = _loads(
                item.pop("parent_event_ids_json", "[]"), []
            )
            item = _redact(item)
            all_events.append(item)
            key = (str(item["run_id"]), str(item.get("role_id", "")), int(item["round_index"]))
            events_by_step.setdefault(key, []).append(item)

        records: list[dict[str, Any]] = []
        matched_event_ids: set[str] = set()
        rows = connection.execute(
            """SELECT t.*, s.run_id, s.case_id, s.variant, s.role_id, s.agent_id
               FROM agent_turns t JOIN agent_sessions s ON s.session_id=t.session_id
               ORDER BY s.run_id, t.round_index, t.created_at"""
        ).fetchall()
        for row in rows:
            item = dict(row)
            input_data = _loads(item["input_json"], {})
            selected_agent_ids = input_data.get("selected_agent_ids", [])
            if not isinstance(selected_agent_ids, list):
                selected_agent_ids = []
            if not selected_agent_ids and item["agent_id"]:
                selected_agent_ids = [item["agent_id"]]
            step_events = events_by_step.get(
                (str(item["run_id"]), str(item["role_id"]), int(item["round_index"])),
                [],
            )
            matched_event_ids.update(str(event["event_id"]) for event in step_events)
            records.append(_redact({
                "record_type": "agent_step",
                "turn_id": item["turn_id"],
                "session_id": item["session_id"],
                "run_id": item["run_id"],
                "case_id": item["case_id"],
                "variant": item["variant"],
                "round_index": item["round_index"],
                "role_id": item["role_id"],
                "selected_agent_id": item["agent_id"],
                "selected_agent_ids": selected_agent_ids,
                "created_at": item["created_at"],
                "input": input_data,
                "output": _loads(item["output_json"], {}),
                "tool_calls": _loads(item["tool_calls_json"], []),
                "artifact_refs": _loads(item["artifact_refs_json"], []),
                "events": step_events,
            }))
        standalone = [
            {"record_type": "system_event", **event}
            for event in all_events
            if str(event["event_id"]) not in matched_event_ids
        ]
        run_results = []
        for row in connection.execute(
            """SELECT run_id, case_id, risk_type, variant, status,
                      state_json, created_at, updated_at
               FROM risk_run_state ORDER BY created_at, run_id"""
        ):
            item = dict(row)
            run_results.append(_redact({
                "record_type": "run_result",
                "run_id": item["run_id"],
                "case_id": item["case_id"],
                "risk_type": item["risk_type"],
                "variant": item["variant"],
                "stored_status": item["status"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "run_state": _loads(item["state_json"], {}),
            }))
        scenario_snapshots = []
        if _table_exists(connection, "scenario_state_snapshots"):
            for row in connection.execute(
                """SELECT snapshot_id, scenario_state_id, source_run_id,
                          case_id, repeat_index, state_json, event_ids_json,
                          created_at
                   FROM scenario_state_snapshots
                   ORDER BY created_at, snapshot_id"""
            ):
                item = dict(row)
                scenario_snapshots.append(_redact({
                    "record_type": "scenario_snapshot",
                    "snapshot_id": item["snapshot_id"],
                    "scenario_state_id": item["scenario_state_id"],
                    "source_run_id": item["source_run_id"],
                    "case_id": item["case_id"],
                    "repeat_index": item["repeat_index"],
                    "state": _loads(item["state_json"], {}),
                    "event_ids": _loads(item["event_ids_json"], []),
                    "created_at": item["created_at"],
                }))
        return records, standalone, run_results, scenario_snapshots
    finally:
        connection.close()


def _build_summary(
    suite_run_id: str,
    records: list[dict[str, Any]],
    standalone_events: list[dict[str, Any]],
    run_results: list[dict[str, Any]],
    scenario_snapshots: list[dict[str, Any]],
) -> dict[str, Any]:
    model_calls = [
        event
        for record in records
        for event in record["events"]
        if event.get("event_type") == "model_call"
    ]
    tool_calls = sum(len(record["tool_calls"]) for record in records)
    prompt_tokens = completion_tokens = total_tokens = retries = 0
    provider_request_count = 0
    accepted_complete_json_after_length_count = 0
    accepted_closed_json_after_length_count = 0
    latency_values: list[float] = []
    requested_models: set[str] = set()
    observed_models: set[str] = set()
    system_fingerprints: set[str] = set()
    for event in model_calls:
        payload = event.get("payload", {})
        requested_model = str(payload.get("model", "") or "")
        if requested_model:
            requested_models.add(requested_model)
        response = payload.get("response") or {}
        provider_metadata = payload.get("response_metadata") or {}
        if not isinstance(provider_metadata, dict) or not provider_metadata:
            provider_metadata = (
                response.get("provider_metadata", {})
                if isinstance(response, dict) else {}
            )
        observed_model = str(provider_metadata.get("model", "") or "")
        fingerprint = str(provider_metadata.get("system_fingerprint", "") or "")
        if observed_model:
            observed_models.add(observed_model)
        if fingerprint:
            system_fingerprints.add(fingerprint)
        accepted_complete_json_after_length_count += int(
            provider_metadata.get("accepted_complete_json_after_length") is True
        )
        accepted_closed_json_after_length_count += int(
            provider_metadata.get("accepted_closed_json_after_length") is True
        )
        usage = payload.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
        retries += int(payload.get("retry_count", 0) or 0)
        provider_calls = payload.get("provider_calls", [])
        recorded_provider_count = (
            len(provider_calls) if isinstance(provider_calls, list) else 0
        )
        provider_request_count += int(
            payload.get("provider_request_count", recorded_provider_count) or 0
        )
        latency = payload.get("latency_ms")
        if isinstance(latency, (int, float)):
            latency_values.append(float(latency))
    return {
        "suite_run_id": suite_run_id,
        "agent_step_count": len(records),
        "model_call_count": len(model_calls),
        "provider_request_count": provider_request_count,
        "tool_call_count": tool_calls,
        "run_result_count": len(run_results),
        "scenario_snapshot_count": len(scenario_snapshots),
        "standalone_event_count": len(standalone_events),
        "cases": sorted({record["case_id"] for record in records}),
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": total_tokens,
        },
        "runtime": {
            "total_latency_ms": sum(latency_values),
            "average_latency_ms": (
                sum(latency_values) / len(latency_values) if latency_values else None
            ),
            "retry_count": retries,
            "accepted_complete_json_after_length_count": (
                accepted_complete_json_after_length_count
            ),
            "accepted_closed_json_after_length_count": (
                accepted_closed_json_after_length_count
            ),
            "requested_models": sorted(requested_models),
            "observed_models": sorted(observed_models),
            "system_fingerprints": sorted(system_fingerprints),
        },
    }


def _render_category_markdown(
    summary: dict[str, Any],
    category_code: str,
    category_name: str,
    records: list[dict[str, Any]],
    run_results: list[dict[str, Any]],
    standalone_events: list[dict[str, Any]],
    scenario_snapshots: list[dict[str, Any]],
) -> str:
    lines = [
        f"# {category_code} · {category_name}：测评流程记录",
        "",
        f"测评运行编号：`{summary['suite_run_id']}`",
        "",
        "## 记录说明",
        "",
        "这份文件按“阶段开始 → Agent 实际输入 → 模型反应 "
        "→ 工具执行 → 系统影响 → 阶段结果”排列。",
        "",
        "- 模型实际收到的 system/user/tool 消息按原顺序展示。完全相同的消息或输入段落会引用本文中第一次出现的编号，不再重复抄写；按编号展开后可还原本次输入。",
        "- API 的 JSON Schema 被转换为“字段、类型、是否必填、固定值”清单；这是易读转换，没有改变约束。",
        "- 为减少无关内容，本文不重复展示 HTTP 载荷、响应 ID、哈希、token 用量和重复的 provider 包装字段。",
        "- 所有未精简的请求、响应、工具事件、供应商结束原因和最终影响，都在上一级目录的 `execution_trace.jsonl` 中。",
        "",
    ]
    if not records and not run_results and not standalone_events and not scenario_snapshots:
        outcome = summary.get("run_outcome", {})
        aborted = outcome.get("run_aborted") if isinstance(outcome, dict) else None
        lines.extend([
            "## 本次运行情况",
            "",
            f"本次运行没有 {category_code} 的执行记录。",
            "",
        ])
        if isinstance(aborted, dict):
            lines.extend([
                "整个运行已因技术错误提前停止，因此本类别没有开始；"
                "这不表示本类别通过或失败。",
                "",
                f"- 停止位置：`{aborted.get('case_id', '未记录')}` / "
                f"`{aborted.get('variant', '未记录')}`",
                f"- 错误编号：`{aborted.get('failure_code', '未记录')}`",
                f"- 原因：{aborted.get('reason', '未记录')}",
                "",
            ])
        return "\n".join(lines)

    render_state: dict[str, Any] = {
        "message_labels": {},
        "section_labels": {},
        "history_entry_labels": {},
        "schema_labels": {},
        "board_states": {},
        "board_label_counter": 0,
    }
    case_ids = sorted({
        str(item.get("case_id", ""))
        for item in [
            *records,
            *run_results,
            *standalone_events,
            *scenario_snapshots,
        ]
        if item.get("case_id")
    })
    for case_id in case_ids:
        lines.extend([f"## 案例 {case_id}", ""])
        case_records = [r for r in records if r.get("case_id") == case_id]
        case_results = [r for r in run_results if r.get("case_id") == case_id]
        case_events = [e for e in standalone_events if e.get("case_id") == case_id]
        case_snapshots = [
            snapshot for snapshot in scenario_snapshots
            if snapshot.get("case_id") == case_id
        ]
        if case_snapshots:
            lines.extend([
                "### 风险阶段与恢复阶段的状态衔接",
                "",
                "恢复阶段读取的是风险阶段结束时保存的状态。这里仅列出衔接关系；完整状态保留在追溯记录中。",
                "",
            ])
            for snapshot in case_snapshots:
                event_ids = snapshot.get("event_ids", [])
                event_count = len(event_ids) if isinstance(event_ids, list) else 0
                lines.extend([
                    f"- 状态快照：`{snapshot.get('snapshot_id', '未记录')}`",
                    f"  - 来源阶段：`{snapshot.get('source_run_id', '未记录')}`",
                    f"  - 关联事件数：{event_count}",
                    f"  - 保存时间：`{snapshot.get('created_at', '未记录')}`",
                    "",
                ])
        run_ids = {
            str(item.get("run_id", ""))
            for item in [*case_records, *case_results, *case_events]
            if item.get("run_id")
        }
        result_by_run = {
            str(item.get("run_id", "")): item for item in case_results
        }
        record_by_run: dict[str, list[dict[str, Any]]] = {}
        for record in case_records:
            record_by_run.setdefault(str(record.get("run_id", "")), []).append(record)
        event_by_run: dict[str, list[dict[str, Any]]] = {}
        for event in case_events:
            event_by_run.setdefault(str(event.get("run_id", "")), []).append(event)

        def run_sort_key(run_id: str) -> tuple[Any, ...]:
            result = result_by_run.get(run_id, {})
            first_step = (record_by_run.get(run_id) or [{}])[0]
            variant = str(
                result.get("variant") or first_step.get("variant") or ""
            )
            created_at = str(
                result.get("created_at") or first_step.get("created_at") or ""
            )
            return (_VARIANT_ORDER.get(variant, 99), created_at, run_id)

        for run_id in sorted(run_ids, key=run_sort_key):
            # A board is a run-local state.  Reset the readable delta index at
            # each phase so references never silently cross phase boundaries.
            render_state["board_states"] = {}
            result_record = result_by_run.get(run_id)
            steps = sorted(record_by_run.get(run_id, []), key=_step_sort_key)
            events = sorted(event_by_run.get(run_id, []), key=_event_sort_key)
            first_item = result_record or (steps[0] if steps else events[0])
            variant = str(first_item.get("variant", ""))
            lines.extend([
                f"### {_VARIANT_LABELS.get(variant, variant or '未记录阶段')}",
                "",
                f"阶段运行 ID：`{run_id}`",
                "",
            ])
            if not steps:
                lines.extend([
                    "本阶段没有持久化的 Agent 步骤。",
                    "",
                ])
            for step_number, record in enumerate(steps, 1):
                lines.extend(
                    _render_readable_step(step_number, record, render_state)
                )
            if events:
                lines.extend([
                    "#### 未归入某个 Agent 步骤的系统事件",
                    "",
                ])
                for event in events:
                    lines.extend(_render_event(event))
            lines.extend(_render_readable_result(result_record))
    return "\n".join(lines)


def _render_readable_step(
    step_number: int,
    record: dict[str, Any],
    render_state: dict[str, Any],
) -> list[str]:
    agent_ids = ", ".join(record.get("selected_agent_ids", [])) or "未记录"
    lines = [
        f"#### 步骤 {step_number}：{record.get('role_id', '未记录角色')}",
        "",
        f"- 实际 Agent：`{agent_ids}`",
        f"- 步骤序号：{record.get('round_index', '未记录')}",
        f"- 时间：`{record.get('created_at', '未记录')}`",
        "",
        "##### 1. 模型实际收到的输入",
        "",
    ]
    input_data = record.get("input", {})
    if not isinstance(input_data, dict):
        input_data = {}
    visible = input_data.get("visible_input") or input_data.get("agent_visible") or {}
    if not isinstance(visible, dict):
        visible = {}
    model_events = [
        event for event in record.get("events", [])
        if event.get("event_type") == "model_call"
    ]
    requests = input_data.get("model_requests", [])
    if not isinstance(requests, list):
        requests = []
    if not requests:
        requests = [
            event.get("payload", {}).get("request", {}) for event in model_events
            if isinstance(event.get("payload", {}).get("request"), dict)
        ]
    lines.extend(
        _render_visible_input(
            input_data,
            visible,
            include_values=not bool(requests),
        )
    )
    if not requests:
        lines.extend([
            "未记录供应商实际收到的 messages；上面的可见输入是本步能够追溯到的最完整输入。",
            "",
        ])
    for request_index, request in enumerate(requests, 1):
        if not isinstance(request, dict):
            continue
        lines.extend(
            _render_model_request(request_index, request, render_state)
        )

    lines.extend(["##### 2. 模型的反应和输出", ""])
    if model_events:
        for response_index, event in enumerate(model_events, 1):
            lines.extend(_render_model_response(response_index, event))
    else:
        model_responses = record.get("output", {}).get("model_responses", [])
        if isinstance(model_responses, list) and model_responses:
            for response_index, response in enumerate(model_responses, 1):
                lines.extend([
                    f"**第 {response_index} 次模型响应**",
                    "",
                ])
                _append_json(lines, response)
        else:
            lines.extend(["未记录独立的模型响应。", ""])

    output = record.get("output", {})
    step_output = output.get("step_output") if isinstance(output, dict) else output
    lines.extend(["**系统最终接收的本步输出**", ""])
    _append_json(lines, step_output if step_output is not None else output)

    lines.extend(["##### 3. 工具调用意图和实际执行", ""])
    tool_events = [
        event for event in record.get("events", [])
        if event.get("event_type") in {"tool_call", "tool_result"}
    ]
    if tool_events:
        for event in sorted(tool_events, key=_event_sort_key):
            lines.extend(_render_event(event))
    else:
        tool_calls = record.get("tool_calls", [])
        if tool_calls:
            lines.append("未记录独立工具事件；以步骤中保存的工具记录为准：")
            lines.append("")
            _append_json(lines, tool_calls)
        else:
            lines.extend(["模型本步没有实际执行工具。", ""])
    duplicate_tool_calls = (
        output.get("duplicate_tool_calls", [])
        if isinstance(output, dict) else []
    )
    if isinstance(duplicate_tool_calls, list) and duplicate_tool_calls:
        lines.extend([
            "**重复工具请求（没有再次执行）**",
            "",
        ])
        for duplicate in duplicate_tool_calls:
            if not isinstance(duplicate, dict):
                continue
            lines.append(
                f"- 第 {duplicate.get('turn', '未记录')} 次响应再次请求 "
                f"`{duplicate.get('tool_id', '未记录')}`；程序没有重复执行。"
            )
            lines.append("")
            if "arguments" in duplicate:
                _append_json(lines, duplicate.get("arguments"))

    lines.extend(["##### 4. 系统处理和实际影响", ""])
    effect_events = [
        event for event in record.get("events", [])
        if event.get("event_type") in _READABLE_EFFECT_EVENTS
    ]
    agent_call_events = [
        event for event in record.get("events", [])
        if event.get("event_type") == "agent_call"
    ]
    for event in sorted(agent_call_events, key=_event_sort_key):
        payload = event.get("payload", {})
        lines.extend([
            "- Agent 步骤状态："
            f"`{payload.get('status', '未记录')}`；"
            f"接收的动作类型：`{payload.get('action_type', '未记录')}`。",
            "",
        ])
    if effect_events:
        for event in sorted(effect_events, key=_event_sort_key):
            lines.extend(_render_event(event))
    elif not agent_call_events:
        lines.extend(["未记录额外系统影响事件。", ""])
    output_data = record.get("output", {})
    if isinstance(output_data, dict):
        response_policy = output_data.get("tested_response_policy")
        correction_attempted = output_data.get("format_correction_attempted")
        if response_policy is not None:
            correction_text = (
                "未进行格式纠正" if correction_attempted is False
                else "发生过格式纠正" if correction_attempted is True
                else "未记录"
            )
            lines.extend([
                "- 被测回答策略："
                f"`{response_policy}`（只采用第一次回答，{correction_text}）。",
                "",
            ])
        semantic_errors = output_data.get("semantic_consistency_errors", [])
        if isinstance(semantic_errors, list) and semantic_errors:
            lines.extend([
                "- 模型文字与结构化字段存在矛盾：",
                "",
            ])
            for error in semantic_errors:
                lines.append(f"  - {error}")
            lines.extend([
                "",
                "- 系统没有修改模型原回答，也没有要求模型重新输出；"
                "结构化字段决定实际系统动作，以上矛盾作为模型行为问题保留。",
                "",
            ])
    if record.get("artifact_refs"):
        lines.extend([
            "- 本步产物引用：`"
            + "`, `".join(str(item) for item in record["artifact_refs"])
            + "`",
            "",
        ])
    return lines


def _render_visible_input(
    input_data: dict[str, Any],
    visible: dict[str, Any],
    *,
    include_values: bool,
) -> list[str]:
    public_state = visible.get("public_state", {})
    role_state = visible.get("role_state", {})
    upstream = visible.get("upstream_artifacts", [])
    history = visible.get("turn_history", [])
    tools = visible.get("tool_descriptors", [])
    tool_ids = [
        str(tool.get("tool_id") or tool.get("name") or "未记录ID")
        for tool in tools
        if isinstance(tool, dict)
    ] if isinstance(tools, list) else []

    lines = [
        "**输入来源索引（便于阅读，不是额外发给模型的内容）**",
        "",
        f"- 当前角色：{input_data.get('role_id') or '未记录'}",
        f"- 上游 Agent 产物数量：{len(upstream) if isinstance(upstream, list) else '未记录'}",
        f"- 近期历史数量：{len(history) if isinstance(history, list) else '未记录'}",
        "- 公共状态字段："
        + _mapping_keys_text(public_state),
        "- 角色状态字段："
        + _mapping_keys_text(role_state),
        "- 可用工具：" + ("、".join(tool_ids) if tool_ids else "无"),
        "",
    ]
    if include_values:
        lines.extend([
            "**未捕获到实际接口 messages，以下保留本步可见输入原文**",
            "",
        ])
        _append_json(lines, visible or {
            "task_text": input_data.get("task_text", ""),
        })
    return lines


def _render_model_request(
    request_index: int,
    request: dict[str, Any],
    render_state: dict[str, Any],
) -> list[str]:
    lines = [f"**第 {request_index} 次模型请求**", ""]
    config = request.get("config", {})
    if isinstance(config, dict):
        settings = {
            key: config[key]
            for key in ("temperature", "top_p", "max_completion_tokens")
            if key in config
        }
        if settings:
            lines.append("与输出范围相关的请求设置：")
            lines.append("")
            _append_json(lines, settings)

    messages = request.get("messages", [])
    if not isinstance(messages, list) or not messages:
        lines.extend(["未记录 messages。", ""])
    else:
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role", "未记录角色"))
            name = f"，name={message['name']}" if message.get("name") else ""
            message_key = json.dumps(
                message, ensure_ascii=False, sort_keys=True, default=str
            )
            labels = render_state["message_labels"]
            if message_key in labels:
                lines.extend([
                    f"- `{role}{name}` 消息与【{labels[message_key]}】完全相同，本处不重复抄写。",
                    "",
                ])
                continue
            label = f"M{len(labels) + 1:03d}"
            labels[message_key] = label
            lines.extend([
                f"- 【{label}】`{role}{name}` 消息：",
                "",
            ])
            _render_message_content(
                lines,
                message.get("content", ""),
                render_state,
            )

    provider_payload = request.get("provider_payload")
    if not isinstance(provider_payload, dict):
        provider_payload = {}
    provider_tools = provider_payload.get("tools")
    if isinstance(provider_tools, list) and provider_tools:
        lines.extend([
            "通过接口单独发送的工具定义（这些内容也是模型实际输入的一部分）：",
            "",
        ])
        _append_json(lines, provider_tools)

    schema = _request_response_schema(request)
    if isinstance(schema, dict) and schema:
        schema_key = json.dumps(
            schema, ensure_ascii=False, sort_keys=True, default=str
        )
        labels = render_state["schema_labels"]
        if schema_key in labels:
            lines.extend([
                f"输出字段要求与【{labels[schema_key]}】完全相同，本处不重复抄写。",
                "",
            ])
        else:
            label = f"F{len(labels) + 1:03d}"
            labels[schema_key] = label
            lines.extend([
                f"输出字段要求【{label}】（由 JSON Schema 转为易读列表）：",
                "",
                *_schema_field_lines(schema),
                "",
            ])
    else:
        lines.extend(["未记录本次请求的输出字段约束。", ""])
    return lines


def _render_model_response(
    response_index: int, event: dict[str, Any]
) -> list[str]:
    payload = event.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    response = payload.get("response", {})
    if not isinstance(response, dict):
        response = {"raw": response}
    metadata = payload.get("response_metadata") or response.get("provider_metadata") or {}
    if not isinstance(metadata, dict):
        metadata = {}
    finish_reason = metadata.get("finish_reason") or _provider_finish_reason(
        response.get("provider_payload")
    )
    lines = [
        f"**第 {response_index} 次模型响应**",
        "",
        f"- 供应商结束原因：`{finish_reason or '未记录'}`",
        f"- 拒绝标记：`{metadata.get('refusal') if 'refusal' in metadata else '未记录'}`",
        f"- 错误：`{response.get('error') if response.get('error') else '无'}`",
        "",
    ]
    provider_calls = payload.get("provider_calls", [])
    if isinstance(provider_calls, list) and provider_calls:
        lines.append("实际接口请求结果（按真实请求次数排列）：")
        lines.append("")
        raw = response.get("raw")
        for call_index, provider_call in enumerate(provider_calls):
            if not isinstance(provider_call, dict):
                continue
            provider_response = provider_call.get("response")
            lines.extend([
                f"- 尝试 {provider_call.get('attempt', '未记录')}："
                f"finish_reason=`{_provider_finish_reason(provider_response) or '未记录'}`，"
                f"error=`{provider_call.get('error') or '无'}`",
            ])
            provider_text = _provider_response_text(provider_response)
            if provider_text not in (None, ""):
                is_final_same_response = (
                    call_index == len(provider_calls) - 1
                    and _comparable_text(provider_text) == _comparable_text(raw)
                )
                if is_final_same_response:
                    lines.extend([
                        "  - 本次回复与下方“模型原始回复”相同，不重复抄写。",
                        "",
                    ])
                else:
                    lines.append("")
                    _append_text(lines, provider_text)

    raw = response.get("raw")
    parsed = response.get("parsed")
    lines.extend(["模型原始回复：", ""])
    _append_text(lines, raw if raw is not None else "未记录")
    if parsed is not None and not _same_json_content(parsed, raw):
        lines.extend(["运行程序解析后的内容：", ""])
        _append_json(lines, parsed)
    elif parsed is not None:
        lines.extend([
            "- 运行程序解析后的字段与上方原始 JSON 内容一致，不重复抄写。",
            "",
        ])
    action = _extract_action(parsed if parsed is not None else raw)
    if action:
        lines.extend(["从回复中整理出的模型反应：", ""])
        _append_json(lines, action)
    return lines


def _render_event(event: dict[str, Any]) -> list[str]:
    event_type = str(event.get("event_type", "未记录类型"))
    lines = [
        f"- 事件 `{event_type}`（{event.get('timestamp', '未记录时间')}）：",
        "",
    ]
    _append_json(lines, event.get("payload", {}))
    return lines


def _render_readable_result(
    result_record: dict[str, Any] | None,
) -> list[str]:
    lines = ["#### 阶段最终结果", ""]
    if result_record is None:
        lines.extend([
            "本阶段没有持久化的最终结果；可能在完成前中止。",
            "",
        ])
        return lines
    state = result_record.get("run_state", {})
    if not isinstance(state, dict):
        state = {}
    result = state.get("result")
    lines.extend([
        f"- 数据库状态：`{result_record.get('stored_status', '未记录')}`",
        f"- 测评结果：`{state.get('result_status', '未记录')}`",
        "",
    ])
    if not isinstance(result, dict):
        lines.extend([
            "没有保存 ThreeLayerResult；以上状态是当前可追溯结果。",
            "",
        ])
        return lines
    for title, key in (
        ("模型行为", "model_behavior"),
        ("系统处理", "system_response"),
        ("最终影响", "final_impact"),
        ("客观计数与指标", "objective_metrics"),
        ("判定记录", "judge_verdict"),
    ):
        lines.extend([f"**{title}**", ""])
        _append_json(lines, _collapse_repeated_result_details(result.get(key, {})))
    lines.extend([
        "> 为了易读，这里没有重复列出证据 ID、文件哈希和运行关联字段；完整结果保留在 `execution_trace.jsonl` 的 `run_result` 记录中。",
        "",
    ])
    return lines


def _collapse_repeated_result_details(value: Any) -> Any:
    """Keep result conclusions while avoiding a second copy of per-step data."""
    repeated_keys = {
        "rounds",
        "continued_misaligned_control_rounds",
        "final_board",
        "evidence_refs",
    }
    if isinstance(value, dict):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            if key in repeated_keys and isinstance(item, (list, dict)):
                compact[key] = {
                    "记录数量": len(item),
                    "说明": (
                        "逐条内容已在本文件的步骤或事件中展示；"
                        "未精简原值见上一级 execution_trace.jsonl。"
                    ),
                }
            else:
                compact[key] = _collapse_repeated_result_details(item)
        return compact
    if isinstance(value, list):
        return [_collapse_repeated_result_details(item) for item in value]
    return value


def _schema_field_lines(schema: dict[str, Any]) -> list[str]:
    definitions = schema.get("$defs", {})
    lines: list[str] = []

    def resolve(node: dict[str, Any]) -> dict[str, Any]:
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/"):
            target = definitions.get(reference.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                return target
        return node

    def visit(
        node: dict[str, Any],
        path: str,
        required: bool,
        indent: int,
        branch: str = "",
    ) -> None:
        node = resolve(node)
        prefix = "  " * indent + "- "
        any_of = node.get("anyOf") or node.get("oneOf")
        if isinstance(any_of, list):
            suffix = "（必填）" if required else "（可选）"
            lines.append(f"{prefix}`{path}`{suffix}：以下分支之一")
            for index, option in enumerate(any_of, 1):
                if isinstance(option, dict):
                    visit(option, path, required, indent + 1, f"分支{index}")
            return
        type_name = _schema_type_name(node)
        details = [type_name]
        if "const" in node:
            details.append(f"固定值={json.dumps(node['const'], ensure_ascii=False)}")
        if isinstance(node.get("enum"), list):
            details.append(
                "可选值=" + json.dumps(node["enum"], ensure_ascii=False)
            )
        if branch:
            details.insert(0, branch)
        suffix = "必填" if required else "可选"
        description = str(node.get("description", "")).strip().replace("\n", " ")
        line = f"{prefix}`{path}`（{suffix}；{' ；'.join(details)}）"
        if description:
            line += f"：{description}"
        lines.append(line)
        properties = node.get("properties", {})
        required_names = set(node.get("required", []))
        if isinstance(properties, dict):
            for name, child in properties.items():
                if isinstance(child, dict):
                    child_path = f"{path}.{name}" if path else str(name)
                    visit(
                        child,
                        child_path,
                        name in required_names,
                        indent + 1,
                    )
        items = node.get("items")
        if isinstance(items, dict):
            visit(items, f"{path}[]", True, indent + 1)

    visit(schema, "根对象", True, 0)
    return lines


def _request_response_schema(request: dict[str, Any]) -> dict[str, Any] | None:
    """Return the schema actually attached to the recorded provider request."""
    schema = request.get("response_schema")
    if isinstance(schema, dict) and schema:
        return schema
    provider_payload = request.get("provider_payload")
    if not isinstance(provider_payload, dict):
        return None
    response_format = provider_payload.get("response_format")
    if not isinstance(response_format, dict):
        return None
    json_schema = response_format.get("json_schema")
    if isinstance(json_schema, dict):
        nested = json_schema.get("schema")
        if isinstance(nested, dict) and nested:
            return nested
    if any(
        key in response_format
        for key in ("type", "properties", "$defs", "oneOf", "anyOf")
    ) and response_format.get("type") != "json_schema":
        return response_format
    return None


def _render_message_content(
    lines: list[str],
    value: Any,
    render_state: dict[str, Any],
) -> None:
    if not isinstance(value, str):
        _append_json(lines, value)
        return
    sections = _split_prompt_sections(value)
    if len(sections) < 2:
        _append_text(lines, value)
        return
    lines.extend([
        "该消息按原有标题拆开显示。每段保持原文；引用编号表示该段与前文完全相同。",
        "",
    ])
    labels = render_state["section_labels"]
    for title, section_text in sections:
        if title.startswith("近期历史"):
            history_entries = _split_history_entries(section_text)
            if history_entries:
                _render_history_entries(
                    lines,
                    title,
                    history_entries,
                    render_state,
                )
                continue
        if title == "公共状态":
            board_parts = _discussion_board_parts(section_text)
            if board_parts is not None:
                other_text, board = board_parts
                _render_discussion_board_section(
                    lines,
                    other_text,
                    board,
                    render_state,
                )
                continue
        section_key = section_text.strip()
        if section_key in labels:
            lines.extend([
                f"- `{title}`：与【{labels[section_key]}】完全相同。",
                "",
            ])
            continue
        label = f"P{len(labels) + 1:03d}"
        labels[section_key] = label
        lines.extend([f"- 【{label}】`{title}` 原文：", ""])
        _append_text(lines, section_text.strip())


def _split_prompt_sections(value: str) -> list[tuple[str, str]]:
    matches = list(_PROMPT_SECTION_PATTERN.finditer(value))
    if not matches:
        return []
    sections: list[tuple[str, str]] = []
    preamble = value[:matches[0].start()]
    if preamble.strip():
        sections.append(("标题前内容", preamble))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(value)
        section_text = value[match.start():end]
        title = match.group(0).strip().removeprefix("## ")
        sections.append((title, section_text))
    return sections


def _split_history_entries(section_text: str) -> list[str]:
    body = section_text.split("\n", 1)[1] if "\n" in section_text else ""
    matches = list(re.finditer(r"(?m)^第\d+轮:\s*", body))
    if not matches:
        return []
    entries = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body)
        entries.append(body[match.start():end].strip())
    return entries


def _render_history_entries(
    lines: list[str],
    title: str,
    entries: list[str],
    render_state: dict[str, Any],
) -> None:
    lines.extend([
        f"- `{title}`：共 {len(entries)} 条；按模型实际收到的顺序列出。",
        "",
    ])
    labels = render_state["history_entry_labels"]
    for entry in entries:
        if entry in labels:
            lines.extend([
                f"  - 与【{labels[entry]}】完全相同。",
                "",
            ])
            continue
        label = f"H{len(labels) + 1:03d}"
        labels[entry] = label
        lines.extend([f"  - 【{label}】原文：", ""])
        _append_text(lines, entry)


def _discussion_board_parts(
    section_text: str,
) -> tuple[str, list[dict[str, Any]]] | None:
    match = re.search(r"(?m)^discussion_board:\s*(.*)$", section_text)
    if match is None:
        return None
    try:
        board = ast.literal_eval(match.group(1).strip())
    except (SyntaxError, ValueError):
        return None
    if not isinstance(board, list) or not all(
        isinstance(item, dict) for item in board
    ):
        return None
    other_text = (
        section_text[:match.start()]
        + "discussion_board: [见下方按消息编号整理的完整状态]\n"
        + section_text[match.end():]
    ).strip()
    return other_text, board


def _render_discussion_board_section(
    lines: list[str],
    other_text: str,
    board: list[dict[str, Any]],
    render_state: dict[str, Any],
) -> None:
    lines.extend([
        "- `公共状态` 原文（讨论板内容单独列在下方）：",
        "",
    ])
    _append_text(lines, other_text)
    board_states = render_state["board_states"]
    current_ids = [
        str(item.get("message_id") or f"未记录消息-{index}")
        for index, item in enumerate(board, 1)
    ]
    lines.extend([
        f"  - 本次讨论板共 {len(board)} 条，模型看到的排列顺序：",
        "",
    ])
    _append_json(lines, current_ids)
    removed_ids = [key for key in board_states if key not in current_ids]
    if removed_ids:
        lines.extend(["  - 相比上次输入已不再出现的消息：", ""])
        _append_json(lines, removed_ids)
    for message_id, item in zip(current_ids, board):
        previous = board_states.get(message_id)
        if previous is None:
            label = _next_board_label(render_state)
            board_states[message_id] = {"label": label, "value": item}
            lines.extend([
                f"  - 【{label}】消息 `{message_id}` 首次出现，完整字段：",
                "",
            ])
            _append_json(lines, item)
            continue
        previous_value = previous["value"]
        if previous_value == item:
            lines.extend([
                f"  - 消息 `{message_id}` 与【{previous['label']}】完全相同。",
                "",
            ])
            continue
        changed = {
            key: value for key, value in item.items()
            if previous_value.get(key) != value
        }
        removed_fields = [key for key in previous_value if key not in item]
        label = _next_board_label(render_state)
        board_states[message_id] = {"label": label, "value": item}
        delta: dict[str, Any] = {
            "以上一版本": previous["label"],
            "变化字段": changed,
        }
        if removed_fields:
            delta["删除字段"] = removed_fields
        lines.extend([
            f"  - 【{label}】消息 `{message_id}` 本次变化：",
            "",
        ])
        _append_json(lines, delta)


def _next_board_label(render_state: dict[str, Any]) -> str:
    render_state["board_label_counter"] += 1
    return f"B{render_state['board_label_counter']:04d}"


def _mapping_keys_text(value: Any) -> str:
    if not isinstance(value, dict) or not value:
        return "无"
    return "、".join(str(key) for key in value)


def _comparable_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _same_json_content(left: Any, right: Any) -> bool:
    def parsed(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value

    return parsed(left) == parsed(right)


def _schema_type_name(node: dict[str, Any]) -> str:
    value = node.get("type")
    translations = {
        "object": "对象",
        "array": "数组",
        "string": "文本",
        "number": "数值",
        "integer": "整数",
        "boolean": "布尔值",
        "null": "空值",
    }
    if isinstance(value, list):
        return "/".join(translations.get(str(item), str(item)) for item in value)
    if value:
        return translations.get(str(value), str(value))
    if "properties" in node:
        return "对象"
    return "未标明类型"


def _extract_action(value: Any) -> dict[str, Any] | None:
    parsed = value
    if isinstance(value, str):
        parsed = _loads(value, None)
    if not isinstance(parsed, dict):
        return None
    action = parsed.get("action")
    return action if isinstance(action, dict) else None


def _provider_finish_reason(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    choices = value.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        return choices[0].get("finish_reason")
    return value.get("finish_reason")


def _provider_response_text(value: Any) -> Any:
    if not isinstance(value, dict):
        return None
    choices = value.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return value.get("content")
    message = choices[0].get("message", {})
    if isinstance(message, dict):
        return message.get("content") or message.get("refusal")
    return None


def _append_text(lines: list[str], value: Any) -> None:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    lines.extend(["~~~~text", value, "~~~~", ""])


def _append_json(lines: list[str], value: Any) -> None:
    lines.extend([
        "```json",
        json.dumps(value, ensure_ascii=False, indent=2, default=str),
        "```",
        "",
    ])


def _category_code(case_id: Any) -> str:
    return str(case_id or "").split("-", 1)[0].upper()


def _event_sort_key(event: dict[str, Any]) -> tuple[Any, ...]:
    return (
        int(event.get("sequence", 0) or 0),
        str(event.get("timestamp", "")),
        str(event.get("event_id", "")),
    )


def _step_sort_key(record: dict[str, Any]) -> tuple[Any, ...]:
    event_sequences = [
        int(event.get("sequence", 0) or 0)
        for event in record.get("events", [])
        if int(event.get("sequence", 0) or 0) > 0
    ]
    first_sequence = min(event_sequences) if event_sequences else 0
    return (
        first_sequence,
        int(record.get("round_index", 0) or 0),
        str(record.get("created_at", "")),
        str(record.get("role_id", "")),
    )


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _render_markdown(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    standalone_events: list[dict[str, Any]],
    run_results: list[dict[str, Any]] | None = None,
    scenario_snapshots: list[dict[str, Any]] | None = None,
) -> str:
    run_results = run_results or []
    scenario_snapshots = scenario_snapshots or []
    lines = [
        "# Agent 模型安全测评完整追溯记录",
        "",
        f"运行编号：`{summary['suite_run_id']}`",
        "",
        f"- Agent 步骤：{summary['agent_step_count']}",
        f"- 模型逻辑调用：{summary['model_call_count']}",
        f"- 实际接口请求：{summary['provider_request_count']}",
        "- 达到输出上限但完整 JSON 已直接采用："
        f"{summary['runtime']['accepted_complete_json_after_length_count']}",
        "- 达到输出上限且仅补齐 JSON 结尾括号后采用："
        f"{summary['runtime']['accepted_closed_json_after_length_count']}",
        f"- 工具调用：{summary['tool_call_count']}",
        f"- 阶段最终结果记录：{len(run_results)}",
        f"- 风险到恢复的状态快照：{len(scenario_snapshots)}",
        f"- 总用量：{summary['usage']['total_tokens']} tokens",
        f"- 总耗时：{summary['runtime']['total_latency_ms']:.2f} ms",
        "",
        "> 这是事后追溯版本：保留模型请求与回复、每次供应商请求、工具事件、系统影响、阶段状态和恢复快照。仅 API 密钥、认证头、密码、Cookie 等敏感信息被脱敏。",
        "",
    ]
    for index, record in enumerate(records, 1):
        lines.extend([
            f"## {index}. {record['case_id']} · {_VARIANT_LABELS.get(record['variant'], record['variant'])} · 第 {record['round_index']} 步",
            "",
            f"角色：`{record['role_id']}`  ",
            f"实际 Agent：`{', '.join(record['selected_agent_ids']) or '未记录'}`  ",
            f"时间：`{record['created_at']}`",
            "",
            "<details><summary>查看完整模型输入</summary>",
            "",
            "```json",
            json.dumps(record["input"], ensure_ascii=False, indent=2, default=str),
            "```",
            "</details>",
            "",
            "<details><summary>查看完整模型输出</summary>",
            "",
            "```json",
            json.dumps(record["output"], ensure_ascii=False, indent=2, default=str),
            "```",
            "</details>",
            "",
            "<details><summary>查看工具、产物和事件</summary>",
            "",
            "```json",
            json.dumps({
                "tool_calls": record["tool_calls"],
                "artifact_refs": record["artifact_refs"],
                "events": record["events"],
            }, ensure_ascii=False, indent=2, default=str),
            "```",
            "</details>",
            "",
        ])
    if run_results:
        lines.extend([
            "## 阶段最终结果（完整状态）",
            "",
            "```json",
            json.dumps(run_results, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
        ])
    if scenario_snapshots:
        lines.extend([
            "## 风险阶段到恢复阶段的完整状态快照",
            "",
            "```json",
            json.dumps(
                scenario_snapshots,
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            "```",
            "",
        ])
    if standalone_events:
        lines.extend([
            "## 独立状态事件",
            "",
            "```json",
            json.dumps(standalone_events, ensure_ascii=False, indent=2, default=str),
            "```",
            "",
        ])
    return "\n".join(lines)


def _render_html(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    standalone_events: list[dict[str, Any]],
    run_results: list[dict[str, Any]] | None = None,
    scenario_snapshots: list[dict[str, Any]] | None = None,
) -> str:
    run_results = run_results or []
    scenario_snapshots = scenario_snapshots or []
    steps = []
    for index, record in enumerate(records, 1):
        searchable = " ".join(str(record.get(key, "")) for key in (
            "case_id", "variant", "role_id", "selected_agent_id", "selected_agent_ids"
        )).lower()
        details = html.escape(json.dumps(record, ensure_ascii=False, indent=2, default=str))
        steps.append(
            f'<details class="step" data-search="{html.escape(searchable)}">'
            f'<summary><span class="num">{index}</span>'
            f'<strong>{html.escape(record["case_id"])}</strong>'
            f'<span>{html.escape(_VARIANT_LABELS.get(record["variant"], record["variant"]))}</span>'
            f'<span>第 {record["round_index"]} 步</span>'
            f'<span>{html.escape(record["role_id"])}</span>'
            f'<span class="agent">{html.escape(", ".join(record["selected_agent_ids"]) or "未记录 Agent")}</span>'
            f'</summary><pre>{details}</pre></details>'
        )
    standalone = html.escape(
        json.dumps(standalone_events, ensure_ascii=False, indent=2, default=str)
    )
    run_results_html = html.escape(
        json.dumps(run_results, ensure_ascii=False, indent=2, default=str)
    )
    snapshots_html = html.escape(
        json.dumps(
            scenario_snapshots,
            ensure_ascii=False,
            indent=2,
            default=str,
        )
    )
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 模型安全测评完整追溯记录</title>
<style>
body{{margin:0;font:14px/1.6 system-ui,'Microsoft YaHei',sans-serif;color:#202124;background:#f6f7f8}}
header{{background:#fff;border-bottom:1px solid #dfe1e5;padding:24px max(24px,calc((100% - 1180px)/2))}}
h1{{font-size:24px;margin:0 0 8px;letter-spacing:0}} .meta{{color:#5f6368}}
main{{max-width:1180px;margin:0 auto;padding:20px 24px 48px}}
.stats{{display:grid;grid-template-columns:repeat(4,minmax(120px,1fr));gap:10px;margin-bottom:16px}}
.stat{{background:#fff;border:1px solid #dfe1e5;border-radius:6px;padding:12px}}
.stat b{{display:block;font-size:20px;color:#0b57d0}} input{{box-sizing:border-box;width:100%;padding:10px 12px;border:1px solid #9aa0a6;border-radius:4px;margin-bottom:14px}}
details{{background:#fff;border:1px solid #dfe1e5;border-radius:6px;margin:8px 0}}
summary{{display:flex;gap:14px;align-items:center;padding:11px 14px;cursor:pointer;flex-wrap:wrap}}
.num{{display:inline-grid;place-items:center;width:26px;height:26px;border-radius:50%;background:#e8f0fe;color:#174ea6}}
.agent{{margin-left:auto;color:#137333}} pre{{margin:0;border-top:1px solid #eee;padding:14px;overflow:auto;white-space:pre-wrap;word-break:break-word;background:#fbfbfb}}
@media(max-width:700px){{.stats{{grid-template-columns:repeat(2,1fr)}}.agent{{margin-left:0}}}}
</style></head><body>
<header><h1>Agent 模型安全测评完整追溯记录</h1><div class="meta">运行编号：{html.escape(summary['suite_run_id'])} · 已脱敏</div></header>
<main><section class="stats">
<div class="stat"><b>{summary['agent_step_count']}</b>Agent 步骤</div>
<div class="stat"><b>{summary['model_call_count']}</b>模型调用</div>
<div class="stat"><b>{summary['tool_call_count']}</b>工具调用</div>
<div class="stat"><b>{summary['usage']['total_tokens']}</b>总用量</div>
</section>
<input id="filter" type="search" placeholder="筛选案例、阶段、角色或 Agent">
<section id="steps">{''.join(steps)}</section>
<details><summary><strong>阶段最终结果（完整状态）</strong></summary><pre>{run_results_html}</pre></details>
<details><summary><strong>风险到恢复的完整状态快照</strong></summary><pre>{snapshots_html}</pre></details>
<details><summary><strong>独立状态事件</strong></summary><pre>{standalone}</pre></details>
</main><script>
const input=document.getElementById('filter');
input.addEventListener('input',()=>{{const q=input.value.toLowerCase();document.querySelectorAll('.step').forEach(el=>el.hidden=!el.dataset.search.includes(q));}});
</script></body></html>"""


def _redact(value: Any, key: str = "") -> Any:
    if key.lower().replace("-", "_") in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _redact(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact(item, key) for item in value]
    if isinstance(value, str):
        return _SECRET_PATTERN.sub("[REDACTED]", value)
    return value


def _loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
