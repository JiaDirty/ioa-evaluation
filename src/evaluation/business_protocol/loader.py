"""Load business evaluation cases from external JSON or JSONL files.

The runtime still uses the validated ``BusinessCaseSpec`` models.  This module
keeps the storage format independent from the Python case factory: one JSONL
line (or one JSON object in a JSON array) is parsed, validated, and returned to
the existing runner.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from .models import BusinessCaseSpec


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_SCENARIO_DATA_DIR = PROJECT_ROOT / "data" / "scenarios"
SUPPORTED_SUFFIXES = {".json", ".jsonl", ".ndjson"}


class CaseDataLoadError(ValueError):
    """Raised when an external scenario file cannot be read or validated."""


def _read_jsonl(path: Path) -> Iterator[tuple[int, Any]]:
    try:
        handle = path.open("r", encoding="utf-8")
    except OSError as exc:  # pragma: no cover - exercised through public API
        raise CaseDataLoadError(f"cannot open {path}: {exc}") from exc

    with handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                yield line_number, json.loads(line)
            except json.JSONDecodeError as exc:
                raise CaseDataLoadError(
                    f"invalid JSON in {path} at line {line_number}: {exc.msg}"
                ) from exc


def _read_json(path: Path) -> Iterator[tuple[int, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:  # pragma: no cover - exercised through public API
        raise CaseDataLoadError(f"cannot open {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CaseDataLoadError(f"invalid JSON in {path}: {exc.msg}") from exc

    if isinstance(value, list):
        yield from ((index, item) for index, item in enumerate(value, start=1))
        return
    if isinstance(value, dict) and isinstance(value.get("cases"), list):
        yield from (
            (index, item) for index, item in enumerate(value["cases"], start=1)
        )
        return
    if isinstance(value, dict):
        yield 1, value
        return
    raise CaseDataLoadError(
        f"{path} must contain a JSON object, an array, or an object with a cases array"
    )


def _iter_file_payloads(path: Path) -> Iterator[tuple[int, Any]]:
    if path.suffix.lower() in {".jsonl", ".ndjson"}:
        yield from _read_jsonl(path)
    elif path.suffix.lower() == ".json":
        yield from _read_json(path)
    else:  # pragma: no cover - paths are filtered by the caller
        raise CaseDataLoadError(f"unsupported scenario file suffix: {path}")


def _unwrap_case_payload(payload: Any, path: Path, location: int) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise CaseDataLoadError(
            f"{path} at record {location} must be a JSON object"
        )
    # The envelope allows future generation metadata (prompt version, seed,
    # model id) without mixing it into the runtime model.  Direct case objects
    # remain supported for convenient hand-authored files.
    if "case" in payload:
        if not isinstance(payload["case"], dict):
            raise CaseDataLoadError(
                f"{path} at record {location} has a non-object 'case' field"
            )
        return payload["case"]
    return payload


def load_business_cases_from_paths(
    paths: Iterable[Path],
) -> dict[str, BusinessCaseSpec]:
    """Load and validate cases from the supplied JSON/JSONL files.

    Case identifiers must be unique across all files.  Returning newly parsed
    Pydantic objects on every call preserves the previous factory behaviour:
    callers may safely mutate their copy during a run.
    """

    cases: dict[str, BusinessCaseSpec] = {}
    files = sorted(Path(path) for path in paths)
    if not files:
        raise CaseDataLoadError("no scenario data files were supplied")

    for path in files:
        if not path.is_file():
            raise CaseDataLoadError(f"scenario path is not a file: {path}")
        for location, payload in _iter_file_payloads(path):
            raw_case = _unwrap_case_payload(payload, path, location)
            try:
                case = BusinessCaseSpec.model_validate(raw_case)
            except Exception as exc:
                case_id = raw_case.get("case_id", "<missing>")
                raise CaseDataLoadError(
                    f"invalid case {case_id!r} in {path} at record {location}: {exc}"
                ) from exc
            if case.case_id in cases:
                raise CaseDataLoadError(
                    f"duplicate case_id {case.case_id!r} in {path} at record {location}"
                )
            cases[case.case_id] = case

    return cases


def load_business_cases(
    data_dir: Path | str = DEFAULT_SCENARIO_DATA_DIR,
) -> dict[str, BusinessCaseSpec]:
    """Load all current scenario files from ``data_dir``.

    Only direct children with ``.json``, ``.jsonl`` or ``.ndjson`` suffixes are
    considered.  Archive folders and generated candidate batches are therefore
    excluded unless explicitly passed as ``data_dir``.
    """

    directory = Path(data_dir)
    if not directory.is_dir():
        raise CaseDataLoadError(f"scenario data directory does not exist: {directory}")
    paths = [
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    ]
    return load_business_cases_from_paths(paths)


__all__ = [
    "CaseDataLoadError",
    "DEFAULT_SCENARIO_DATA_DIR",
    "load_business_cases",
    "load_business_cases_from_paths",
]
