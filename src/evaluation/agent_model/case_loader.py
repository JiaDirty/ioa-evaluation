"""Strict JSONL case loader with Pydantic validation for v2 dataset.

Loads the 160-case JSONL, validates each line against the appropriate
category-specific Pydantic model, and routes them to per-category files
in data/agent_model_cases/.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Generator

from pydantic import ValidationError

from .models import (
    CATEGORY_MODEL_MAP,
    CATEGORY_TO_RISK,
    CommonCase,
)
from .tools.authoritative_facts import AuthoritativeFactLookup
from .tools.runtime_registry import _authoritative_aliases
from .dataset_split import semantic_duplicate_pairs, split_cases
from .design_validation import validate_case_design

logger = logging.getLogger(__name__)

CASE_FILE_PATTERN = "{category_code}_{risk_type}.jsonl"


class CaseLoadError(Exception):
    """Raised when a case fails schema validation."""

    def __init__(self, case_id: str, message: str, line_number: int = 0):
        self.case_id = case_id
        self.line_number = line_number
        super().__init__(f"[{case_id}] L{line_number}: {message}")


class CaseLoader:
    """Load and validate cases from the v2 JSONL dataset."""

    def __init__(self, jsonl_path: str | Path):
        self.jsonl_path = Path(jsonl_path)
        self._cases: dict[str, CommonCase] = {}
        self._errors: list[CaseLoadError] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load_all(self) -> dict[str, CommonCase]:
        """Load and validate every case. Returns {case_id: case}."""
        self._cases.clear()
        self._errors.clear()
        for line_num, raw in enumerate(self._iter_lines(), start=1):
            try:
                case = self._parse_one(raw, line_num)
                if case.case_id in self._cases:
                    self._errors.append(
                        CaseLoadError(
                            case.case_id,
                            f"Duplicate case_id '{case.case_id}'",
                            line_num,
                        )
                    )
                    continue
                self._cases[case.case_id] = case
            except CaseLoadError as e:
                self._errors.append(e)
        logger.info(
            "Loaded %d valid cases, %d errors from %s",
            len(self._cases),
            len(self._errors),
            self.jsonl_path,
        )
        return self._cases

    def load_by_category(self, category_code: str) -> list[CommonCase]:
        """Return all cases for a given category_code (CAS/CON/...)."""
        self.load_all()
        return [c for c in self._cases.values() if c.category_code == category_code]

    def validate_only(
        self,
        *,
        expected_total: int | None = 160,
        expected_per_category: int | None = 20,
    ) -> dict[str, Any]:
        """Run validation checks without storing cases. Returns a report dict."""
        self.load_all()
        by_category: dict[str, int] = {}
        for c in self._cases.values():
            cat = c.category_code
            by_category[cat] = by_category.get(cat, 0) + 1

        if expected_total is not None and len(self._cases) != expected_total:
            self._errors.append(
                CaseLoadError(
                    "DATASET",
                    f"Expected {expected_total} valid cases, got {len(self._cases)}",
                )
            )

        if expected_per_category is not None:
            for code in CATEGORY_TO_RISK:
                actual = by_category.get(code, 0)
                if actual != expected_per_category:
                    self._errors.append(
                        CaseLoadError(
                            code,
                            f"Expected {expected_per_category} cases, got {actual}",
                        )
                    )

        return {
            "total_lines": len(self._cases)
            + len([error for error in self._errors if error.line_number > 0]),
            "valid_cases": len(self._cases),
            "errors": len(self._errors),
            "by_category": by_category,
            "error_details": [
                {"case_id": e.case_id, "line": e.line_number, "message": str(e)}
                for e in self._errors
            ],
            "split_summary": {
                key: len(value) for key, value in split_cases(self._cases).items()
            },
            "semantic_duplicate_warnings": semantic_duplicate_pairs(self._cases),
        }

    def split_to_files(self, output_dir: str | Path) -> dict[str, Path]:
        """Split loaded cases into per-category JSONL files."""
        self.load_all()
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        written: dict[str, Path] = {}

        by_cat: dict[str, list[CommonCase]] = {}
        for c in self._cases.values():
            by_cat.setdefault(c.category_code, []).append(c)

        for code, cases in by_cat.items():
            risk = CATEGORY_TO_RISK.get(code, code.lower())
            fname = CASE_FILE_PATTERN.format(category_code=code, risk_type=risk)
            fpath = output_dir / fname
            with open(fpath, "w", encoding="utf-8") as fh:
                for case in cases:
                    fh.write(case.model_dump_json(by_alias=True) + "\n")
            written[code] = fpath
            logger.info("Wrote %d cases to %s", len(cases), fpath)

        return written

    @property
    def errors(self) -> list[CaseLoadError]:
        return list(self._errors)

    @property
    def cases(self) -> dict[str, CommonCase]:
        return dict(self._cases)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_lines(self) -> Generator[str, None, None]:
        if not self.jsonl_path.exists():
            raise FileNotFoundError(f"JSONL file not found: {self.jsonl_path}")
        with open(self.jsonl_path, "r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if stripped:
                    yield stripped

    def _parse_one(self, raw: str, line_number: int) -> CommonCase:
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as e:
            raise CaseLoadError(
                "UNKNOWN",
                f"Invalid JSON at line {line_number}: {e}",
                line_number,
            ) from e

        schema_version = obj.get("schema_version")
        if schema_version != "2.0":
            raise CaseLoadError(
                obj.get("case_id", "UNKNOWN"),
                f"Unsupported schema_version '{schema_version}' (expected '2.0')",
                line_number,
            )

        category_code = obj.get("category_code")
        model_cls = CATEGORY_MODEL_MAP.get(category_code)
        if model_cls is None:
            raise CaseLoadError(
                obj.get("case_id", "UNKNOWN"),
                f"Unknown category_code '{category_code}'",
                line_number,
            )

        try:
            case = model_cls.model_validate(obj)
        except ValidationError as e:
            raise CaseLoadError(
                obj.get("case_id", "UNKNOWN"),
                f"Validation failed: {_fmt_validation_error(e)}",
                line_number,
            ) from e

        # Additional structural validations per category
        self._validate_specific(case, line_number)
        design_errors = validate_case_design(case)
        if design_errors:
            raise CaseLoadError(
                case.case_id,
                "Design validation failed: " + "; ".join(design_errors),
                line_number,
            )
        self._validate_tool_fixture_reachability(case, line_number)
        return case

    @staticmethod
    def _validate_specific(case: CommonCase, line_number: int) -> None:
        """Per-category structural checks beyond Pydantic."""
        code = case.category_code
        case_id = case.case_id

        # All roles must use same model config
        mc = case.tested_model_config
        if not mc.same_base_model_for_all_roles:
            raise CaseLoadError(
                case_id,
                "same_base_model_for_all_roles must be True",
                line_number,
            )

        # Verify category_code matches case_id prefix (soft check)
        expected_prefix = code
        if not case_id.startswith(expected_prefix):
            logger.warning(
                "Case %s: case_id prefix doesn't match category_code %s",
                case_id, code,
            )

    @staticmethod
    def _validate_tool_fixture_reachability(
        case: CommonCase,
        line_number: int,
    ) -> None:
        """Ensure model-visible public IDs can reach controlled fixtures."""
        scenario = case.scenario
        fixture = getattr(scenario, "authoritative_tool_fixture", None)
        if fixture is not None:
            lookup = AuthoritativeFactLookup(
                fixture.model_dump(mode="json"),
                aliases=_authoritative_aliases(case),
            )
            public_ids = _public_lookup_ids(case)
            if not public_ids:
                raise CaseLoadError(
                    case.case_id,
                    "authoritative fixture exists but no public lookup id is visible",
                    line_number,
                )
            if not any(lookup.query(lookup_id=item).get("semantic_success") for item in public_ids):
                raise CaseLoadError(
                    case.case_id,
                    "authoritative fixture is not reachable through public visible ids",
                    line_number,
                )

        if case.category_code == "INC":
            fixtures = list(getattr(scenario, "fixture_catalog", []))
            fixture_keys = {item.fixture_key for item in fixtures}
            for task in getattr(scenario, "task_sequence", []):
                if task.fixture_key not in fixture_keys:
                    raise CaseLoadError(
                        case.case_id,
                        f"INC task {task.task_id} references missing fixture_key {task.fixture_key}",
                        line_number,
                    )
            claims = [
                str(item.visible_input.get("claim_text", "")).strip()
                for item in fixtures
            ]
            if len(claims) != 30 or len(set(claims)) != 30:
                raise CaseLoadError(
                    case.case_id,
                    "INC requires 30 distinct round materials within each case",
                    line_number,
                )
            fixture_by_key = {item.fixture_key: item for item in fixtures}
            for phase in ("baseline", "misaligned", "recovery"):
                phase_support = {
                    fixture_by_key[task.fixture_key].authoritative_tool_response.get(
                        "supported"
                    )
                    for task in scenario.task_sequence
                    if task.phase == phase
                }
                if phase_support != {False, True}:
                    raise CaseLoadError(
                        case.case_id,
                        f"INC {phase} rounds require both supported and unsupported materials",
                        line_number,
                    )


def _fmt_validation_error(e: ValidationError) -> str:
    """Format a Pydantic ValidationError into a compact string."""
    errors = []
    for err in e.errors():
        loc = ".".join(str(p) for p in err["loc"])
        msg = err["msg"]
        errors.append(f"{loc}: {msg}")
    return "; ".join(errors[:5])  # cap at 5 errors


def _public_lookup_ids(case: CommonCase) -> list[str]:
    scenario = case.scenario
    if case.category_code == "CAS":
        return [getattr(getattr(scenario, "claim", None), "claim_id", "")]
    if case.category_code == "RUM":
        return [getattr(getattr(scenario, "rumor", None), "rumor_id", "")]
    if case.category_code == "CON":
        values: list[str] = []
        values.extend(getattr(getattr(scenario, "ground_truth", None), "authority_source_ids", []) or [])
        for report in getattr(scenario, "reports", []) or []:
            values.append(getattr(report, "report_id", ""))
            values.extend(getattr(report, "source_ids", []) or [])
        return [item for item in values if item]
    return []


# ------------------------------------------------------------------
# Convenience
# ------------------------------------------------------------------

def load_cases(jsonl_path: str | Path) -> CaseLoader:
    """Create a CaseLoader and load all cases."""
    loader = CaseLoader(jsonl_path)
    loader.load_all()
    return loader
