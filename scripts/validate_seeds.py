from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.attacks.registry import DEFAULT_ATTACK_ADAPTER_REGISTRY
from src.experiment.scenario_loader import ScenarioLoader


def main() -> None:
    paths = sorted(Path("data/seeds").glob("seed_*.json"))
    assert len(paths) == 18, len(paths)
    registry = DEFAULT_ATTACK_ADAPTER_REGISTRY
    for path in paths:
        data = json.loads(path.read_text(encoding="utf-8"))
        scenario = ScenarioLoader(path).load()
        assert scenario.attack.attack_type in registry.list_attack_types(), path.name
        assert scenario.attack.adapter == scenario.attack.attack_type, path.name
        assert scenario.attack.objective, path.name
        assert scenario.attack.success_stages, path.name
        assert scenario.attack.required_evidence, path.name
        serialized = json.dumps(data, ensure_ascii=False)
        for token in ["target_sub_ioas", "hop_chain"]:
            assert token not in json.dumps(data.get("task", {}), ensure_ascii=False), f"{path.name}: {token}"
        assert "evaluation" in data, path.name
        assert data["evaluation"]["success_stages"], path.name
        assert data["evaluation"]["required_evidence"], path.name
    print("validate_seeds: OK")


if __name__ == "__main__":
    main()
