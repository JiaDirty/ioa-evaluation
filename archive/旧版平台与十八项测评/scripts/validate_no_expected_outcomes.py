from __future__ import annotations

from pathlib import Path


def main() -> None:
    banned = [
        "attack_" + "should_" + "succeed",
        "expected_" + "attack_" + "success",
        "expected_" + "blocked",
        "expected_" + "verdict",
        "expected_" + "outcome",
        "\"passed\": valid",
        "passed " + "= " + "valid",
    ]
    roots = [Path("data"), Path("src"), Path("api"), Path("tests"), Path("docs"), Path("run_experiment.py")]
    hits: list[str] = []
    for root in roots:
        paths = [root] if root.is_file() else [p for p in root.rglob("*") if p.is_file()]
        for path in paths:
            if any(part in {".venv", "node_modules", "__pycache__"} for part in path.parts):
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in banned:
                if token in text:
                    hits.append(f"{path}:{token}")
    assert not hits, "\n".join(hits)
    print("validate_no_expected_result_fields: OK")


if __name__ == "__main__":
    main()
