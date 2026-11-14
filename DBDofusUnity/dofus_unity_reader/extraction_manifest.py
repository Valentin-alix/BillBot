import hashlib
import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

PIPELINE_VERSION = "2026-11-14-maps-typed-references-v2"

_HASH_CHUNK_SIZE = 1024 * 1024


class BundleFingerprint(BaseModel, frozen=True):
    size: int
    modified_ns: int

    @classmethod
    def from_path(cls, path: Path) -> "BundleFingerprint":
        stat_result = path.stat()
        return cls(size=stat_result.st_size, modified_ns=stat_result.st_mtime_ns)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(_HASH_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


class ManifestEntry(BaseModel):
    fingerprint: BundleFingerprint
    content_hash: str
    outputs: list[str] = Field(default_factory=list)


class ManifestData(BaseModel):
    pipeline_version: str = PIPELINE_VERSION
    bundles: dict[str, ManifestEntry] = Field(default_factory=dict)


def _normalise_path(path: Path) -> str:
    return str(path.resolve())


class ExtractionManifest:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.data = ManifestData()

    @classmethod
    def load(cls, path: Path) -> "ExtractionManifest":
        manifest = cls(path)
        if not path.exists():
            return manifest

        try:
            data = ManifestData.model_validate_json(path.read_text(encoding="utf-8"))
        except ValidationError:
            return manifest

        if data.pipeline_version != PIPELINE_VERSION:
            return manifest

        manifest.data = data
        return manifest

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(
            dir=self.path.parent, prefix=f"{self.path.name}.", suffix=".tmp"
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as temp_file:
                temp_file.write(f"{self.data.model_dump_json(indent=2)}\n")
            temp_path.replace(self.path)
        finally:
            temp_path.unlink(missing_ok=True)

    def is_up_to_date(self, source_path: Path, *, output_paths: list[Path] | None = None) -> bool:
        source_key = _normalise_path(source_path)
        entry = self.data.bundles.get(source_key)
        if entry is None:
            return False

        current_fingerprint = BundleFingerprint.from_path(source_path)
        if entry.fingerprint != current_fingerprint:
            if entry.content_hash != _hash_file(source_path):
                return False
            entry = entry.model_copy(update={"fingerprint": current_fingerprint})
            self.data.bundles[source_key] = entry

        paths_to_check = (
            output_paths if output_paths is not None else [Path(output_path) for output_path in entry.outputs]
        )
        return all(path.exists() for path in paths_to_check)

    def mark_success(self, source_path: Path, *, output_paths: list[Path]) -> None:
        self.data.bundles[_normalise_path(source_path)] = ManifestEntry(
            fingerprint=BundleFingerprint.from_path(source_path),
            content_hash=_hash_file(source_path),
            outputs=sorted(_normalise_path(path) for path in output_paths),
        )
