"""Offline catalog checks that run before any provider request."""

from __future__ import annotations

import re
from datetime import datetime

from .models import BusinessCaseSpec
from .prompt import build_api_tools, build_user_message


FORBIDDEN_VISIBLE_MARKERS = (
    "ground_truth", "hidden_truth", "judge_label", "安全测评", "风险标签",
)
CATEGORY_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(CAS|CON|RUM|NOR|INC|JUD|DIS|AGE|TRA|CRE)(?![A-Za-z0-9])"
)
EXPECTED_CATEGORIES = {"CAS", "CON", "RUM", "NOR", "INC", "JUD", "DIS", "AGE", "TRA", "CRE"}


def validate_case_catalog(cases: dict[str, BusinessCaseSpec]) -> None:
    if len(cases) < len(EXPECTED_CATEGORIES):
        raise ValueError(f"expected at least {len(EXPECTED_CATEGORIES)} cases, got {len(cases)}")
    actual_categories = {case.category for case in cases.values()}
    if actual_categories != EXPECTED_CATEGORIES:
        missing = EXPECTED_CATEGORIES - actual_categories
        unexpected = actual_categories - EXPECTED_CATEGORIES
        raise ValueError(f"expected categories {sorted(EXPECTED_CATEGORIES)}; "
                         f"missing={sorted(missing)} unexpected={sorted(unexpected)}")
    for case_id, case in cases.items():
        if case_id != case.case_id:
            raise ValueError(f"case key mismatch: {case_id} != {case.case_id}")
        if not case.steps:
            raise ValueError(f"case {case_id} has no steps")
        all_steps = [*case.steps, *case.recovery_steps]
        all_step_ids = [step.step_id for step in all_steps]
        if len(all_step_ids) != len(set(all_step_ids)):
            raise ValueError(f"case {case_id} has duplicate step IDs")
        for flow in (case.steps, case.recovery_steps):
            step_positions = {step.step_id: index for index, step in enumerate(flow)}
            for step in flow:
                for dependency in step.upstream_step_ids:
                    if dependency not in step_positions:
                        raise ValueError(
                            f"step {step.step_id} has unknown dependency {dependency}"
                        )
                    if step_positions[dependency] >= step_positions[step.step_id]:
                        raise ValueError(
                            f"step {step.step_id} dependency must appear earlier: {dependency}"
                        )
        for step in all_steps:
            if set(step.inputs) != {"baseline", "mechanism", "recovery"}:
                raise ValueError(f"step {step.step_id} does not define all conditions")
            names = [tool.name for tool in step.tools]
            if len(names) != len(set(names)):
                raise ValueError(f"step {step.step_id} has duplicate tool names")
            build_api_tools(step.tools)
            for condition in ("baseline", "mechanism", "recovery"):
                visible_current_time = step.current_time_for(condition)
                current_time = datetime.fromisoformat(visible_current_time)
                current_input = step.input_for(condition)
                for item in [
                    *current_input.records,
                    *current_input.upstream_artifacts,
                ]:
                    if datetime.fromisoformat(item.created_at) > current_time:
                        raise ValueError(
                            f"future-visible item in {step.step_id}/{condition}: "
                            f"{item.created_at} > {visible_current_time}"
                        )
                visible = build_user_message(step, condition)
                match = CATEGORY_ID_PATTERN.search(visible)
                if match:
                    raise ValueError(
                        f"model-visible category ID in {step.step_id}: {match.group(0)}"
                    )
                for marker in FORBIDDEN_VISIBLE_MARKERS:
                    if marker in visible:
                        raise ValueError(
                            f"model-visible construct leakage in {step.step_id}: {marker}"
                        )
