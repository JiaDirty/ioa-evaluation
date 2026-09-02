#!/usr/bin/env python
"""Migrate Registry effect stages without changing scenario artifacts."""

from __future__ import annotations

import argparse
import json
import shutil
from collections import Counter
from pathlib import Path


def migrate_registry(path: Path) -> dict[str, object]:
    path = path.expanduser().resolve()
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("entries")
    if not isinstance(entries, dict):
        raise ValueError("registry entries must be an object")

    backup = path.with_suffix(path.suffix + ".pre-stage-migration")
    if not backup.exists():
        shutil.copy2(path, backup)

    changed = 0
    for entry in entries.values():
        if entry.get("stage") != "EFFECT_READY":
            continue
        effect_ref = (entry.get("artifacts") or {}).get("effect") or {}
        effect_path = path.parent / str(effect_ref.get("path", ""))
        if not effect_path.is_file():
            continue
        effect = json.loads(effect_path.read_text(encoding="utf-8"))
        if effect.get("status") == "DRAFT":
            entry["stage"] = "EFFECT_DRAFT"
            changed += 1

    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    stages = Counter(entry.get("stage") for entry in entries.values())
    return {
        "registry": str(path),
        "backup": str(backup),
        "entry_count": len(entries),
        "changed": changed,
        "stage_counts": dict(sorted(stages.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("registry", type=Path, nargs="+")
    args = parser.parse_args(argv)
    print(json.dumps([migrate_registry(path) for path in args.registry], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
