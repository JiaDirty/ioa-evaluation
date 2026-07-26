"""Export the authoritative SQLite trace into readable research artifacts."""

from __future__ import annotations

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
}
_SECRET_PATTERN = re.compile(r"(?i)(bearer\s+|sk-)[a-z0-9._-]{12,}")

_VARIANT_LABELS = {
    "baseline": "基准过程",
    "risk": "风险过程",
    "recovery": "恢复过程",
}


def export_execution_trace(
    db_path: Path,
    output_dir: Path,
    *,
    suite_run_id: str,
) -> dict[str, Any]:
    records, standalone_events = _load_records(db_path)
    summary = _build_summary(suite_run_id, records, standalone_events)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "execution_trace.jsonl"
    markdown_path = output_dir / "execution_trace.md"
    html_path = output_dir / "execution_trace.html"
    summary_path = output_dir / "trace_summary.json"

    jsonl_records = [
        {"record_type": "summary", **summary},
        *records,
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
        _render_markdown(summary, records, standalone_events), encoding="utf-8"
    )
    html_path.write_text(
        _render_html(summary, records, standalone_events), encoding="utf-8"
    )
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    files = [jsonl_path, markdown_path, html_path, summary_path]
    return {
        "record_count": len(records),
        "standalone_event_count": len(standalone_events),
        "files": {
            path.name: {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "size_bytes": path.stat().st_size,
            }
            for path in files
        },
        "usage": summary["usage"],
        "runtime": summary["runtime"],
    }


def _load_records(db_path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
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
        return records, standalone
    finally:
        connection.close()


def _build_summary(
    suite_run_id: str,
    records: list[dict[str, Any]],
    standalone_events: list[dict[str, Any]],
) -> dict[str, Any]:
    model_calls = [
        event
        for record in records
        for event in record["events"]
        if event.get("event_type") == "model_call"
    ]
    tool_calls = sum(len(record["tool_calls"]) for record in records)
    prompt_tokens = completion_tokens = total_tokens = retries = 0
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
        usage = payload.get("usage") or {}
        prompt_tokens += int(usage.get("prompt_tokens", 0) or 0)
        completion_tokens += int(usage.get("completion_tokens", 0) or 0)
        total_tokens += int(usage.get("total_tokens", 0) or 0)
        retries += int(payload.get("retry_count", 0) or 0)
        latency = payload.get("latency_ms")
        if isinstance(latency, (int, float)):
            latency_values.append(float(latency))
    return {
        "suite_run_id": suite_run_id,
        "agent_step_count": len(records),
        "model_call_count": len(model_calls),
        "tool_call_count": tool_calls,
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
            "requested_models": sorted(requested_models),
            "observed_models": sorted(observed_models),
            "system_fingerprints": sorted(system_fingerprints),
        },
    }


def _render_markdown(
    summary: dict[str, Any],
    records: list[dict[str, Any]],
    standalone_events: list[dict[str, Any]],
) -> str:
    lines = [
        "# Agent 模型安全测评执行记录",
        "",
        f"运行编号：`{summary['suite_run_id']}`",
        "",
        f"- Agent 步骤：{summary['agent_step_count']}",
        f"- 模型调用：{summary['model_call_count']}",
        f"- 工具调用：{summary['tool_call_count']}",
        f"- 总用量：{summary['usage']['total_tokens']} tokens",
        f"- 总耗时：{summary['runtime']['total_latency_ms']:.2f} ms",
        "",
        "> 记录已经对 API 密钥、认证头、密码、Cookie 等敏感信息进行脱敏。",
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
) -> str:
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
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 模型安全测评执行记录</title>
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
<header><h1>Agent 模型安全测评执行记录</h1><div class="meta">运行编号：{html.escape(summary['suite_run_id'])} · 已脱敏</div></header>
<main><section class="stats">
<div class="stat"><b>{summary['agent_step_count']}</b>Agent 步骤</div>
<div class="stat"><b>{summary['model_call_count']}</b>模型调用</div>
<div class="stat"><b>{summary['tool_call_count']}</b>工具调用</div>
<div class="stat"><b>{summary['usage']['total_tokens']}</b>总用量</div>
</section>
<input id="filter" type="search" placeholder="筛选案例、阶段、角色或 Agent">
<section id="steps">{''.join(steps)}</section>
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
