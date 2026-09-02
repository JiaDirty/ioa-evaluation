"""Atomic artifact persistence: read, write, hash and verify.

Every durable artifact of the production pipeline is written through this
store so that all writes are atomic, all reads are hash-verified and every
reference stays project-relative.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .models import ArtifactRef


def file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_text(path: str | Path, text: str) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".artifact.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def atomic_write_json(path: str | Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n")


class ArtifactStore:
    """Real hash-addressed artifact store used by the orchestrator."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    # -- write ----------------------------------------------------------------

    def write_text(self, relative_path: str | Path, text: str) -> ArtifactRef:
        target = self.root / Path(relative_path)
        atomic_write_text(target, text)
        return ArtifactRef(
            path=target.relative_to(self.root).as_posix(),
            sha256=file_digest(target),
            schema_version="text",
        )

    def write_json(self, relative_path: str | Path, value: Any, *, schema_version: str, depends_on: list[str] | None = None) -> ArtifactRef:
        target = self.root / Path(relative_path)
        atomic_write_json(target, value)
        return ArtifactRef(
            path=target.relative_to(self.root).as_posix(),
            sha256=file_digest(target),
            schema_version=schema_version,
            depends_on=list(depends_on or []),
        )

    def write_model(self, relative_path: str | Path, model: BaseModel, *, depends_on: list[str] | None = None) -> ArtifactRef:
        target = self.root / Path(relative_path)
        atomic_write_text(target, model.model_dump_json(indent=2) + "\n")
        schema_version = str(model.model_dump(mode="json").get("schema_version", "unknown"))
        return ArtifactRef(
            path=target.relative_to(self.root).as_posix(),
            sha256=file_digest(target),
            schema_version=schema_version,
            depends_on=list(depends_on or []),
        )

    def write_raw_bytes(self, relative_path: str | Path, content: bytes) -> ArtifactRef:
        target = self.root / Path(relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".artifact.", dir=str(target.parent))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, target)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        return ArtifactRef(
            path=target.relative_to(self.root).as_posix(),
            sha256=file_digest(target),
            schema_version="raw",
        )

    # -- read ------------------------------------------------------------------

    def path(self, ref: ArtifactRef | None, relative: str | Path | None = None) -> Path:
        if ref is not None:
            return self.root / ref.path
        return self.root / Path(relative)

    def read_text(self, ref: ArtifactRef) -> str:
        return self.path(ref).read_text(encoding="utf-8")

    def read_json(self, ref: ArtifactRef) -> Any:
        return json.loads(self.read_text(ref))

    def read_model(self, ref: ArtifactRef, model_cls: type[BaseModel]) -> BaseModel:
        return model_cls.model_validate_json(self.read_text(ref))

    # -- verify ----------------------------------------------------------------

    def verify(self, ref: ArtifactRef) -> bool:
        path = self.root / ref.path
        return path.is_file() and file_digest(path) == ref.sha256

    def verify_or_raise(self, ref: ArtifactRef, *, artifact_name: str, task_id: str) -> None:
        if not self.verify(ref):
            raise ValueError(f"artifact hash mismatch: {artifact_name} for task {task_id}")

    def reference(
        self,
        relative_path: str | Path,
        *,
        schema_version: str,
        depends_on: list[str] | None = None,
    ) -> ArtifactRef:
        target = self.root / Path(relative_path)
        if not target.is_file():
            raise FileNotFoundError(str(target))
        return ArtifactRef(
            path=target.relative_to(self.root).as_posix(),
            sha256=file_digest(target),
            schema_version=schema_version,
            depends_on=list(depends_on or []),
        )


__all__ = ["ArtifactStore", "atomic_write_json", "atomic_write_text", "file_digest"]
