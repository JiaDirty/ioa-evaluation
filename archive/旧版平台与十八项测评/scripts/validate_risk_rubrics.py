from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.judging.rubric_registry import DEFAULT_RUBRIC_REGISTRY


def main() -> None:
    registry = DEFAULT_RUBRIC_REGISTRY
    attack_types = registry.list_attack_types()
    assert len(attack_types) == 18, attack_types
    stage_signatures = set()
    for attack_type in attack_types:
        rubric = registry.get(attack_type)
        assert rubric.stages, attack_type
        assert rubric.core_stage in rubric.stages, attack_type
        assert rubric.required_evidence, attack_type
        stage_signatures.add(rubric.stages)
        module_path = Path("src/judging/rubrics") / f"{attack_type}.py"
        assert module_path.exists(), str(module_path)
    assert len(stage_signatures) == 18, "rubrics share identical stage definitions"
    print("validate_risk_rubrics: OK")


if __name__ == "__main__":
    main()
