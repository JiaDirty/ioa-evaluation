"""Risk-specific rubric registry."""

from __future__ import annotations

from dataclasses import dataclass

from .rubrics.base import RiskRubric, build_default_rubrics


class RubricRegistry:
    def __init__(self) -> None:
        self._rubrics: dict[str, RiskRubric] = {
            rubric.attack_type: rubric for rubric in build_default_rubrics()
        }

    def get(self, attack_type: str) -> RiskRubric:
        if attack_type not in self._rubrics:
            raise KeyError(f"No Judge rubric registered for attack type: {attack_type}")
        return self._rubrics[attack_type]

    def list_attack_types(self) -> list[str]:
        return sorted(self._rubrics)


DEFAULT_RUBRIC_REGISTRY = RubricRegistry()
