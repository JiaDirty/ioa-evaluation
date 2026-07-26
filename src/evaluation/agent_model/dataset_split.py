"""Dataset provenance metadata for the development-exposed v2 benchmark."""
from __future__ import annotations

import hashlib
import re
from difflib import SequenceMatcher
from typing import Any


def split_cases(cases: dict[str, Any]) -> dict[str, list[str]]:
    """Describe honest use of v2 cases after all 160 entered development.

    A deterministic post-hoc split cannot turn development-exposed cases into
    a held-out set. Formal execution therefore covers the complete registered
    benchmark and reports the exposure limitation explicitly.
    """
    all_case_ids = sorted(cases)
    return {
        "development_exposed": all_case_ids,
        "formal_evaluation": all_case_ids,
        "held_out": [],
        "controls": [
            "positive_safe_agent",
            "negative_risky_agent",
            "null_no_risk_injection",
            "recovery_no_op",
        ],
    }


def dataset_split_hash(split: dict[str, list[str]]) -> str:
    return hashlib.sha256(repr({key: sorted(value) for key, value in split.items()}).encode()).hexdigest()


def semantic_duplicate_pairs(
    cases: dict[str, Any], threshold: float = 0.92
) -> list[dict[str, Any]]:
    """Flag highly similar model-visible tasks for dataset-review, not scoring."""
    normalized = {
        case_id: re.sub(r"\s+", "", _visible_task_signature(case)).lower()
        for case_id, case in cases.items()
    }
    ids = sorted(cases)
    pairs: list[dict[str, Any]] = []
    for index, left in enumerate(ids):
        for right in ids[index + 1:]:
            if cases[left].category_code != cases[right].category_code:
                continue
            similarity = SequenceMatcher(None, normalized[left], normalized[right]).ratio()
            if similarity >= threshold:
                pairs.append({"left": left, "right": right, "similarity": similarity})
    return pairs


def _visible_task_signature(case: Any) -> str:
    """Match the business text assembled by the category runner."""
    values = [str(case.user_task)]
    if str(case.category_code) == "DIS":
        values.append(str(getattr(case.scenario, "issue", "")))
    return "\n".join(values)
