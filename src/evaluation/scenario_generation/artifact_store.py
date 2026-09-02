"""Hash-addressed artifact helpers used by the pipeline controller."""
from pathlib import Path

from .orchestrator import ArtifactRef, _file_digest


class ArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def digest(self, path: str | Path) -> str:
        return _file_digest(Path(path))

    def reference(self, path: str | Path, *, schema_version: str, depends_on: list[str] | None = None) -> ArtifactRef:
        target = Path(path).resolve()
        return ArtifactRef(path=target.relative_to(self.root).as_posix(), sha256=_file_digest(target), schema_version=schema_version, depends_on=depends_on or [])


__all__ = ["ArtifactRef", "ArtifactStore"]
