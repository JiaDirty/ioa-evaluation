"""Deterministic checks for generated business evaluation candidates."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from ..business_protocol.loader import load_business_cases_from_paths
from ..business_protocol.models import AgentInput, AgentStepSpec, BusinessCaseSpec
from ..catalog import EvaluationCatalog, load_evaluation_catalog
from .models import DeterministicFinding, DeterministicReview


CONDITIONS = ("baseline", "mechanism", "recovery")
VISIBLE_FORBIDDEN_MARKERS = (
    "ground_truth",
    "hidden_truth",
    "judge_label",
    "风险标签",
    "标准答案",
    "安全测评",
)
IDENTIFIER_PATTERN = re.compile(
    r"\b(?:[A-Z]{1,8}-)?[A-Z0-9]*\d[A-Z0-9-]*\b|\d+(?:\.\d+)?",
    re.IGNORECASE,
)
SPACE_PATTERN = re.compile(r"\s+")


@dataclass(frozen=True)
class CandidateRecord:
    case: BusinessCaseSpec
    source_path: Path
    generator_model_id: str
    item_name: str
    batch_id: str

    @property
    def candidate_uid(self) -> str:
        return f"{self.batch_id}::{self.generator_model_id}::{self.case.case_id}"


def discover_candidates(root: Path) -> list[CandidateRecord]:
    records: list[CandidateRecord] = []
    for path in sorted(root.rglob("expanded_cases.jsonl")):
        cases = load_business_cases_from_paths([path])
        model_id = path.parent.name
        batch_id = path.parent.parent.name
        item_name = re.sub(r"__第\d+条$", "", batch_id)
        for case in cases.values():
            records.append(
                CandidateRecord(
                    case=case,
                    source_path=path,
                    generator_model_id=model_id,
                    item_name=item_name,
                    batch_id=batch_id,
                )
            )
    return records


def _finding(
    code: str,
    severity: str,
    location: str,
    message: str,
    *evidence: str,
) -> DeterministicFinding:
    return DeterministicFinding(
        code=code,
        severity=severity,
        location=location,
        message=message,
        evidence=[item for item in evidence if item],
    )


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _stable(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _visible_input(step: AgentStepSpec, condition: str) -> dict[str, Any]:
    payload = step.input_for(condition).model_dump(mode="json")
    return {
        "input": payload,
        "history": step.history_for(condition),
        "current_time": step.current_time_for(condition),
    }


def _condition_projection(step: AgentStepSpec, condition: str) -> dict[str, Any]:
    """Return every fixture that can differ between experimental conditions."""
    visible = _visible_input(step, condition)
    if step.upstream_step_ids:
        visible["input"]["upstream_artifacts"] = []
    return {
        "visible": visible,
        "tools": [
            {
                "name": tool.name,
                "response": tool.responses.get(condition, {}),
                "state_update": tool.state_updates.get(condition, {}),
            }
            for tool in step.tools_for(condition)
        ],
    }


def _all_visible_strings(value: Any, location: str = "") -> Iterable[tuple[str, str]]:
    if isinstance(value, str):
        yield location, value
    elif isinstance(value, dict):
        for key, item in value.items():
            child = f"{location}.{key}" if location else str(key)
            yield from _all_visible_strings(item, child)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _all_visible_strings(item, f"{location}[{index}]")


def _check_step_times(
    step: AgentStepSpec,
    location: str,
    conditions: tuple[str, ...],
) -> list[DeterministicFinding]:
    findings: list[DeterministicFinding] = []
    for condition in conditions:
        current = _parse_time(step.current_time_for(condition))
        if current is None:
            findings.append(
                _finding(
                    "INVALID_CURRENT_TIME",
                    "ERROR",
                    f"{location}.current_time",
                    "步骤时间不是可解析的 ISO 8601 时间。",
                    step.current_time_for(condition),
                )
            )
            continue
        agent_input: AgentInput = step.input_for(condition)
        timed_objects = [
            (record.record_id, record.created_at)
            for record in agent_input.records
        ] + [
            (artifact.artifact_id, artifact.created_at)
            for artifact in agent_input.upstream_artifacts
        ]
        for object_id, created_at in timed_objects:
            created = _parse_time(created_at)
            if created is None:
                findings.append(
                    _finding(
                        "INVALID_OBJECT_TIME",
                        "ERROR",
                        f"{location}.inputs.{condition}",
                        "记录或交接产物时间不是可解析的 ISO 8601 时间。",
                        object_id,
                        created_at,
                    )
                )
            elif created > current:
                findings.append(
                    _finding(
                        "FUTURE_INFORMATION",
                        "ERROR",
                        f"{location}.inputs.{condition}",
                        "步骤看到了在步骤执行时间之后才产生的信息。",
                        object_id,
                        created_at,
                        step.current_time_for(condition),
                    )
                )
    return findings


def _audit_one(record: CandidateRecord, catalog: EvaluationCatalog) -> DeterministicReview:
    case = record.case
    category = next(item for item in catalog.categories if item.code == case.category)
    findings: list[DeterministicFinding] = []
    steps = list(case.steps) + list(case.recovery_steps)
    step_ids = [step.step_id for step in steps]
    if len(step_ids) != len(set(step_ids)):
        findings.append(
            _finding(
                "DUPLICATE_STEP_ID",
                "ERROR",
                "steps",
                "主流程与恢复流程中存在重复步骤编号。",
            )
        )

    seen: set[str] = set()
    for flow_name, flow, visible_conditions in (
        ("steps", case.steps, ("baseline", "mechanism")),
        ("recovery_steps", case.recovery_steps, ("recovery",)),
    ):
        for index, step in enumerate(flow):
            location = f"{flow_name}[{index}]"
            unknown = sorted(set(step.upstream_step_ids) - seen)
            if unknown:
                findings.append(
                    _finding(
                        "INVALID_UPSTREAM_REFERENCE",
                        "ERROR",
                        f"{location}.upstream_step_ids",
                        "步骤引用了尚未出现或不存在的上游步骤。",
                        *unknown,
                    )
                )
            seen.add(step.step_id)
            findings.extend(_check_step_times(step, location, visible_conditions))

            tool_names = [tool.name for tool in step.tools]
            if len(tool_names) != len(set(tool_names)):
                findings.append(
                    _finding(
                        "DUPLICATE_TOOL_NAME",
                        "ERROR",
                        f"{location}.tools",
                        "同一步骤存在重复工具名，运行时调用会产生歧义。",
                    )
                )
            if step.reference:
                missing_tools = sorted(
                    set(step.reference.objective_unsafe_tools) - set(tool_names)
                )
                if missing_tools:
                    findings.append(
                        _finding(
                            "UNKNOWN_UNSAFE_TOOL",
                            "ERROR",
                            f"{location}.reference.objective_unsafe_tools",
                            "客观危险工具清单引用了本步骤不存在的工具。",
                            *missing_tools,
                        )
                    )
            for condition in visible_conditions:
                visible = _visible_input(step, condition)
                for text_location, text in _all_visible_strings(visible):
                    marker = next(
                        (item for item in VISIBLE_FORBIDDEN_MARKERS if item.lower() in text.lower()),
                        None,
                    )
                    if marker:
                        findings.append(
                            _finding(
                                "VISIBLE_EVALUATION_LEAK",
                                "ERROR",
                                f"{location}.{condition}.{text_location}",
                                "模型可见信息泄露了测评或判分提示。",
                                marker,
                            )
                        )

    if not case.steps:
        findings.append(_finding("NO_MAIN_STEP", "ERROR", "steps", "主流程没有步骤。"))
    target_steps = [step for step in case.steps if step.key_node_target]
    if not target_steps:
        findings.append(
            _finding("NO_KEY_TARGET", "ERROR", "steps", "没有可作为主要判分节点的步骤。")
        )
    differing_targets = [
        step.step_id
        for step in target_steps
        if _stable(_condition_projection(step, "baseline"))
        != _stable(_condition_projection(step, "mechanism"))
    ]
    differing_main_steps = [
        step.step_id
        for step in case.steps
        if _stable(_condition_projection(step, "baseline"))
        != _stable(_condition_projection(step, "mechanism"))
    ]
    initial_state_differs = _stable(case.initial_state.get("baseline", {})) != _stable(
        case.initial_state.get("mechanism", {})
    )
    if not differing_main_steps and not initial_state_differs:
        findings.append(
            _finding(
                "NO_CAUSAL_CONTRAST",
                "ERROR",
                "steps",
                "正常条件与风险条件在主要判分节点和初始状态中完全相同，无法形成因果对照。",
            )
        )

    if case.recovery_steps:
        main_tool_outputs = {
            str(value)
            for step in case.steps
            for tool in step.tools
            for condition_payload in tool.responses.values()
            for value in condition_payload.values()
            if isinstance(value, (str, int, float))
        }
        recovery_text = _stable(
            [step.model_dump(mode="json") for step in case.recovery_steps]
        )
        linked = [value for value in main_tool_outputs if len(value) >= 4 and value in recovery_text]
        if not linked:
            findings.append(
                _finding(
                    "UNBOUND_RECOVERY",
                    "WARNING",
                    "recovery_steps",
                    "恢复流程未明显引用主流程工具返回的实际编号，需语义审核确认是否真实绑定风险产物。",
                )
            )
    else:
        has_state_changing_risk_tool = any(
            tool.risk_level in {"high", "critical"}
            and (any(tool.state_updates.values()) or bool(tool.state_bindings))
            for step in case.steps
            for tool in step.tools
        )
        if has_state_changing_risk_tool:
            findings.append(
                _finding(
                    "MISSING_RECOVERY_FLOW",
                    "WARNING",
                    "recovery_steps",
                    "主流程存在高风险状态变更工具但没有恢复步骤，需确认该影响是否可逆或是否应提供处置路径。",
                )
            )

    metadata_sub = str(case.metadata.get("sub_mechanism", ""))
    if case.category == "TRA" and metadata_sub not in {"drift", "vague"}:
        findings.append(
            _finding(
                "INVALID_TRUST_VARIANT",
                "ERROR",
                "metadata.sub_mechanism",
                "信任与授权链必须明确标记 drift 或 vague 子机制。",
                metadata_sub,
            )
        )

    return DeterministicReview(
        candidate_uid=record.candidate_uid,
        case_id=case.case_id,
        category_code=case.category,
        category_name_zh=category.name_zh,
        generator_model_id=record.generator_model_id,
        source_path=str(record.source_path),
        passed=not any(item.severity == "ERROR" for item in findings),
        findings=findings,
        metrics={
            "main_step_count": len(case.steps),
            "recovery_step_count": len(case.recovery_steps),
            "tool_count": sum(len(step.tools) for step in steps),
            "key_target_count": len(target_steps),
            "differing_key_target_count": len(differing_targets),
            "differing_main_step_count": len(differing_main_steps),
            "scoring_contract_version": (
                case.scoring_contract.contract_version
                if case.scoring_contract is not None
                else "legacy"
            ),
            "step_scoring_rule_count": (
                len(case.scoring_contract.step_rules)
                if case.scoring_contract is not None
                else 0
            ),
            "impact_scoring_rule_count": (
                len(case.scoring_contract.impact_rules)
                if case.scoring_contract is not None
                else 0
            ),
        },
    )


def _duplicate_signature(case: BusinessCaseSpec) -> str:
    metadata = case.metadata
    parts = [
        case.title,
        case.purpose,
        str(metadata.get("industry_domain", "")),
        str(metadata.get("business_action", "")),
        str(metadata.get("chain_structure", "")),
        str(metadata.get("condition_difference", "")),
    ]
    normalized = IDENTIFIER_PATTERN.sub("<ID>", " ".join(parts).lower())
    return SPACE_PATTERN.sub(" ", normalized).strip()


def _exact_content_hash(case: BusinessCaseSpec) -> str:
    value = case.model_dump(mode="json")
    value["case_id"] = "<CASE_ID>"
    return hashlib.sha256(_stable(value).encode("utf-8")).hexdigest()


def audit_candidates(
    records: list[CandidateRecord],
) -> tuple[list[DeterministicReview], list[dict[str, Any]]]:
    catalog = load_evaluation_catalog()
    reviews = [_audit_one(record, catalog) for record in records]
    by_uid = {review.candidate_uid: review for review in reviews}
    duplicates: list[dict[str, Any]] = []

    case_id_groups: dict[str, list[CandidateRecord]] = {}
    for record in records:
        case_id_groups.setdefault(record.case.case_id, []).append(record)
    for case_id, group in case_id_groups.items():
        if len(group) < 2:
            continue
        duplicates.append(
            {
                "kind": "DUPLICATE_CASE_ID",
                "case_id": case_id,
                "category_code": group[0].case.category,
                "candidate_uids": [item.candidate_uid for item in group],
                "count": len(group),
            }
        )
        for record in group:
            review = by_uid[record.candidate_uid]
            review.findings.append(
                _finding(
                    "DUPLICATE_CASE_ID",
                    "ERROR",
                    "case_id",
                    "该编号在本轮其他候选中重复，合并数据集时会覆盖或拒绝加载。",
                    *[
                        item.candidate_uid
                        for item in group
                        if item.candidate_uid != record.candidate_uid
                    ][:5],
                )
            )
            review.passed = False

    exact_seen: dict[str, CandidateRecord] = {}
    for record in records:
        digest = _exact_content_hash(record.case)
        if digest in exact_seen:
            other = exact_seen[digest]
            duplicates.append(
                {
                    "kind": "EXACT_CONTENT",
                    "candidate_uid_a": other.candidate_uid,
                    "candidate_uid_b": record.candidate_uid,
                    "case_id_a": other.case.case_id,
                    "case_id_b": record.case.case_id,
                    "category_code": record.case.category,
                    "similarity": 1.0,
                }
            )
        else:
            exact_seen[digest] = record

    by_category: dict[str, list[CandidateRecord]] = {}
    for record in records:
        by_category.setdefault(record.case.category, []).append(record)
    for category_records in by_category.values():
        for index, left in enumerate(category_records):
            left_signature = _duplicate_signature(left.case)
            for right in category_records[index + 1 :]:
                score = SequenceMatcher(
                    None, left_signature, _duplicate_signature(right.case)
                ).ratio()
                if score >= 0.9:
                    duplicates.append(
                        {
                            "kind": "HIGH_SUMMARY_SIMILARITY",
                            "candidate_uid_a": left.candidate_uid,
                            "candidate_uid_b": right.candidate_uid,
                            "case_id_a": left.case.case_id,
                            "case_id_b": right.case.case_id,
                            "category_code": left.case.category,
                            "similarity": round(score, 4),
                        }
                    )

    for pair in duplicates:
        if pair["kind"] == "DUPLICATE_CASE_ID":
            continue
        severity = "ERROR" if pair["kind"] == "EXACT_CONTENT" else "WARNING"
        for current, other in (
            (pair["candidate_uid_a"], pair["candidate_uid_b"]),
            (pair["candidate_uid_b"], pair["candidate_uid_a"]),
        ):
            review = by_uid.get(current)
            if review is None:
                continue
            review.findings.append(
                _finding(
                    pair["kind"],
                    severity,
                    "case",
                    "候选与同类别另一条数据高度重复。",
                    other,
                    str(pair["similarity"]),
                )
            )
            review.passed = not any(item.severity == "ERROR" for item in review.findings)
    return reviews, duplicates
