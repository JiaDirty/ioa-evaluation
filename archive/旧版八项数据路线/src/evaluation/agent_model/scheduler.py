"""Pre-registered deterministic, category-interleaved case scheduling."""

from __future__ import annotations

import random
from collections import defaultdict, deque
from typing import Any, Iterable


def planned_case_order(cases: Iterable[Any], seed: int) -> list[Any]:
    by_category: dict[str, list[Any]] = defaultdict(list)
    for case in cases:
        by_category[str(case.category_code)].append(case)
    rng = random.Random(seed)
    queues: dict[str, deque[Any]] = {}
    for category, values in sorted(by_category.items()):
        values = sorted(values, key=lambda case: case.case_id)
        rng.shuffle(values)
        queues[category] = deque(values)
    categories = sorted(queues)
    rng.shuffle(categories)
    ordered: list[Any] = []
    while any(queues.values()):
        for category in categories:
            if queues[category]:
                ordered.append(queues[category].popleft())
    return ordered


def assert_provider_version_stable(observed_versions: Iterable[str]) -> None:
    versions = {value for value in observed_versions if value}
    if len(versions) > 1:
        raise RuntimeError(f"provider model version drift detected: {sorted(versions)}")
