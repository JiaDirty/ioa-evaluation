from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.attacks.registry import DEFAULT_ATTACK_ADAPTER_REGISTRY


def main() -> None:
    registry = DEFAULT_ATTACK_ADAPTER_REGISTRY
    attack_types = registry.list_attack_types()
    assert len(attack_types) == 18, attack_types
    for attack_type in attack_types:
        adapter = registry.create(attack_type)
        assert adapter.attack_type == attack_type
        assert adapter.success_stages, attack_type
        assert adapter.required_evidence, attack_type

    for path in sorted(Path("data/seeds").glob("seed_*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        attack_type = data["attack"]["attack_type"]
        adapter = registry.create(data["attack"].get("adapter") or attack_type)
        assert adapter.attack_type == attack_type, path.name
    print("validate_attack_adapters: OK")


if __name__ == "__main__":
    main()
