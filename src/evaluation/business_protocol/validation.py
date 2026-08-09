"""Offline catalog checks that run before any provider request."""

from __future__ import annotations

import re

from .models import BusinessCaseSpec
from .prompt import build_api_tools, build_user_message


FORBIDDEN_VISIBLE_MARKERS = (
    "ground_truth", "hidden_truth", "judge_label", "安全测评", "风险标签",
)
CATEGORY_ID_PATTERN = re.compile(r"(?<![A-Z])(CAS|CON|RUM|NOR|INC|JUD|DIS|AGE)-")


def validate_case_catalog(cases: dict[str, BusinessCaseSpec]) -> None:
    if len(cases) != 8:
        raise ValueError(f"expected 8 representative cases, got {len(cases)}")
    if len({case.category for case in cases.values()}) != 8:
        raise ValueError("each category must have exactly one representative case")
    for case_id, case in cases.items():
        if case_id != case.case_id:
            raise ValueError(f"case key mismatch: {case_id} != {case.case_id}")
        if not case.steps:
            raise ValueError(f"case {case_id} has no steps")
        step_positions = {step.step_id: index for index, step in enumerate(case.steps)}
        if len(step_positions) != len(case.steps):
            raise ValueError(f"case {case_id} has duplicate step IDs")
        for step in [*case.steps, *case.recovery_steps]:
            for dependency in step.upstream_step_ids:
                if dependency not in step_positions:
                    raise ValueError(
                        f"step {step.step_id} has unknown dependency {dependency}"
                    )
                if step in case.steps and step_positions[dependency] >= step_positions[step.step_id]:
                    raise ValueError(
                        f"step {step.step_id} dependency must appear earlier: {dependency}"
                    )
            if set(step.inputs) != {"baseline", "mechanism", "recovery"}:
                raise ValueError(f"step {step.step_id} does not define all conditions")
            names = [tool.name for tool in step.tools]
            if len(names) != len(set(names)):
                raise ValueError(f"step {step.step_id} has duplicate tool names")
            build_api_tools(step.tools)
            for condition in ("baseline", "mechanism", "recovery"):
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
