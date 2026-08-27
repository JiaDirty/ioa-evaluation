"""Canonical case files and the deterministic combined dataset artifact."""

from __future__ import annotations

from pathlib import Path

from .case_loader import CaseLoader


PROJECT_ROOT = Path(__file__).resolve().parents[3]
CASE_DIR = PROJECT_ROOT / "data" / "agent_model_cases"
GENERATED_DATASET = (
    PROJECT_ROOT
    / "data"
    / "generated"
    / "IoA-Agent模型安全8项测评可执行数据集-v2-160条.jsonl"
)

CATEGORY_FILES = (
    "CAS_cascade_propagation.jsonl",
    "CON_ecosystem_consensus.jsonl",
    "RUM_rumor_spread.jsonl",
    "NOR_norm_drift.jsonl",
    "INC_incentive_mismatch.jsonl",
    "JUD_judgment_surrender.jsonl",
    "DIS_discussion_distortion.jsonl",
    "AGE_agency_erosion.jsonl",
)


def build_dataset_bytes(case_dir: Path = CASE_DIR) -> bytes:
    """Compile compact authoring files into complete v2 JSONL rows."""
    missing = [name for name in CATEGORY_FILES if not (case_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(f"Missing canonical case files: {', '.join(missing)}")
    rows: list[str] = []
    for name in CATEGORY_FILES:
        loader = CaseLoader(case_dir / name)
        cases = loader.load_all()
        if loader.errors:
            raise ValueError(f"Invalid canonical case file: {case_dir / name}")
        rows.extend(case.model_dump_json(by_alias=True) for case in cases.values())
    return ("\n".join(rows) + "\n").encode("utf-8")


def assert_generated_dataset_current(
    case_dir: Path = CASE_DIR,
    generated_dataset: Path = GENERATED_DATASET,
) -> None:
    """Reject a run when the generated compatibility artifact is stale."""
    expected = build_dataset_bytes(case_dir)
    actual = generated_dataset.read_bytes() if generated_dataset.is_file() else b""
    if actual != expected:
        raise RuntimeError(
            "Generated Agent-model dataset is missing or stale. Run "
            "scripts/build_agent_model_dataset.py before evaluation."
        )
