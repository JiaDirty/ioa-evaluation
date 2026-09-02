"""Dataset-level compatibility gates for reference and expandable evaluations."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..catalog import load_evaluation_catalog
from .loader import (
    SUPPORTED_SUFFIXES,
    CaseDataLoadError,
    load_business_cases_from_paths,
)
from .models import BusinessCaseSpec
from .validation import validate_generated_case


PROJECT_ROOT = Path(__file__).resolve().parents[3]
REFERENCE_SOURCE_MANIFEST_PATH = PROJECT_ROOT / "data" / "legacy_reference_manifest.json"
DatasetProfile = Literal["reference_source", "generic_expandable", "mixed"]


class DatasetCompatibilityError(ValueError):
    """Raised when cases do not satisfy the selected dataset profile."""


class ReferenceSourceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(pattern=r"^[A-Z]{3}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReferenceSourceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["legacy_reference_manifest_v1"]
    cases: dict[str, ReferenceSourceEntry]


@dataclass(frozen=True)
class DatasetValidationReport:
    profile: DatasetProfile
    case_count: int
    category_counts: dict[str, int]
    contract_versions: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return {
            "dataset_profile": self.profile,
            "case_count": self.case_count,
            "category_counts": self.category_counts,
            "contract_versions": self.contract_versions,
        }


@dataclass(frozen=True)
class EvaluationDataset:
    cases: dict[str, BusinessCaseSpec]
    source_files: tuple[Path, ...]
    report: DatasetValidationReport


def case_fingerprint(case: BusinessCaseSpec) -> str:
    """Hash the normalized runtime model rather than source-file formatting."""

    # The execution plan was added after the original reference manifest.  An
    # omitted plan and its default value are semantically identical, so the
    # default is excluded to preserve the old reference hashes.  Non-default
    # plans remain part of the fingerprint once a case opts into them.
    dumped = case.model_dump(mode="json")
    # Runtime-only optional fields added after the reference manifest must not
    # invalidate historical cases when they retain their empty defaults.
    for step in [*dumped.get("steps", []), *dumped.get("recovery_steps", [])]:
        if step.get("visible_state_paths") == []:
            step.pop("visible_state_paths", None)
        for tool in step.get("tools", []):
            if tool.get("conditional_state_updates") == []:
                tool.pop("conditional_state_updates", None)
    if dumped.get("execution_plan") == {
        "pairing": "independent",
        "shared_prefix_step_ids": [],
        "baseline_state_overrides": {},
        "recovery_policy": "on_mechanism_unsafe",
        "recovery_step_ids": None,
    }:
        dumped.pop("execution_plan", None)
    canonical = json.dumps(
        dumped,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


@lru_cache(maxsize=1)
def load_reference_source_manifest(
    path: str | Path = REFERENCE_SOURCE_MANIFEST_PATH,
) -> ReferenceSourceManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetCompatibilityError(f"cannot load reference source manifest: {exc}") from exc
    return ReferenceSourceManifest.model_validate(payload)


def discover_scenario_files(
    sources: list[str | Path] | tuple[str | Path, ...],
    *,
    recursive: bool = False,
) -> tuple[Path, ...]:
    """Resolve files and directories into a stable, duplicate-free file list."""

    if not sources:
        raise CaseDataLoadError("at least one scenario file or directory is required")
    discovered: dict[Path, Path] = {}
    for raw_source in sources:
        source = Path(raw_source).expanduser()
        if source.is_file():
            if source.suffix.lower() not in SUPPORTED_SUFFIXES:
                raise CaseDataLoadError(f"unsupported scenario file suffix: {source}")
            discovered[source.resolve()] = source
            continue
        if source.is_dir():
            iterator = source.rglob("*") if recursive else source.iterdir()
            for path in iterator:
                if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
                    discovered[path.resolve()] = path
            continue
        raise CaseDataLoadError(f"scenario source does not exist: {source}")
    files = tuple(sorted(discovered.values(), key=lambda item: str(item.resolve()).lower()))
    if not files:
        raise CaseDataLoadError("scenario sources contain no supported JSON/JSONL files")
    return files


def validate_evaluation_dataset(
    cases: dict[str, BusinessCaseSpec],
    *,
    profile: DatasetProfile,
    require_complete_reference: bool = False,
) -> DatasetValidationReport:
    """Validate one dataset without assuming a fixed category count or size."""

    if not cases:
        raise DatasetCompatibilityError("evaluation dataset is empty")
    for case_id, case in cases.items():
        if case_id != case.case_id:
            raise DatasetCompatibilityError(
                f"case mapping key does not match case_id: {case_id!r} != {case.case_id!r}"
            )
    if profile == "reference_source":
        _validate_reference_source_cases(cases, require_complete=require_complete_reference)
        versions = {"reference_source_v1": len(cases)}
    elif profile == "generic_expandable":
        _validate_generic_expandable_cases(cases)
        versions = dict(sorted(Counter(
            case.scoring_contract.contract_version
            for case in cases.values()
            if case.scoring_contract is not None
        ).items()))
    elif profile == "mixed":
        reference_cases = {
            case_id: case
            for case_id, case in cases.items()
            if case.scoring_contract is None
        }
        generic_cases = {
            case_id: case
            for case_id, case in cases.items()
            if case.scoring_contract is not None
        }
        if reference_cases:
            _validate_reference_source_cases(reference_cases, require_complete=False)
        if generic_cases:
            _validate_generic_expandable_cases(generic_cases)
        versions = dict(
            sorted(
                {
                    **({"reference_source_v1": len(reference_cases)} if reference_cases else {}),
                    **dict(
                        Counter(
                            case.scoring_contract.contract_version
                            for case in generic_cases.values()
                            if case.scoring_contract is not None
                        )
                    ),
                }.items()
            )
        )
    else:  # pragma: no cover - callers and CLI constrain this value
        raise DatasetCompatibilityError(f"unknown dataset profile: {profile}")
    catalog = load_evaluation_catalog()
    names_by_code = {item.code: item.name_zh for item in catalog.categories}
    category_counts = dict(sorted(Counter(
        names_by_code[case.category] for case in cases.values()
    ).items()))
    return DatasetValidationReport(
        profile=profile,
        case_count=len(cases),
        category_counts=category_counts,
        contract_versions=versions,
    )


def load_evaluation_dataset(
    sources: list[str | Path] | tuple[str | Path, ...],
    *,
    profile: DatasetProfile = "generic_expandable",
    recursive: bool = False,
    require_complete_reference: bool = False,
) -> EvaluationDataset:
    files = discover_scenario_files(sources, recursive=recursive)
    cases = load_business_cases_from_paths(files)
    report = validate_evaluation_dataset(
        cases,
        profile=profile,
        require_complete_reference=require_complete_reference,
    )
    return EvaluationDataset(cases=cases, source_files=files, report=report)


def ensure_runtime_case_supported(case: BusinessCaseSpec) -> None:
    """Production runtime accepts only declarative scoring contracts."""

    if case.scoring_contract is None:
        raise DatasetCompatibilityError(
            f"case {case.case_id!r} has no generic scoring contract; "
            "convert reference source data before runtime execution"
        )


def _validate_reference_source_cases(
    cases: dict[str, BusinessCaseSpec],
    *,
    require_complete: bool,
) -> None:
    manifest = load_reference_source_manifest()
    unknown = sorted(set(cases) - set(manifest.cases))
    if unknown:
        raise DatasetCompatibilityError(
            f"reference_source accepts only registered case IDs; unknown={unknown}"
        )
    if require_complete and set(cases) != set(manifest.cases):
        missing = sorted(set(manifest.cases) - set(cases))
        raise DatasetCompatibilityError(f"reference source dataset is incomplete; missing={missing}")
    for case in cases.values():
        if case.scoring_contract is not None:
            raise DatasetCompatibilityError(
                f"reference source case {case.case_id!r} unexpectedly has a generic contract"
            )
        entry = manifest.cases[case.case_id]
        actual = case_fingerprint(case)
        if entry.category != case.category or entry.sha256 != actual:
            raise DatasetCompatibilityError(
                f"case {case.case_id!r} differs from the registered reference source"
            )


def _validate_generic_expandable_cases(cases: dict[str, BusinessCaseSpec]) -> None:
    reserved_ids = set(load_reference_source_manifest().cases)
    collisions = sorted(set(cases) & reserved_ids)
    if collisions:
        raise DatasetCompatibilityError(
            f"generic_expandable case IDs must not reuse reference source IDs: {collisions}"
        )
    for case in cases.values():
        try:
            validate_generated_case(case)
        except ValueError as exc:
            raise DatasetCompatibilityError(
                f"generic case {case.case_id!r} failed strict admission: {exc}"
            ) from exc


__all__ = [
    "DatasetCompatibilityError",
    "DatasetProfile",
    "DatasetValidationReport",
    "EvaluationDataset",
    "REFERENCE_SOURCE_MANIFEST_PATH",
    "case_fingerprint",
    "discover_scenario_files",
    "ensure_runtime_case_supported",
    "load_evaluation_dataset",
    "load_reference_source_manifest",
    "validate_evaluation_dataset",
]
