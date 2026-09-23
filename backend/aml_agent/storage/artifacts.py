"""Controlled, atomic filesystem storage for run artifacts."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from ._json import dumps as json_dumps
from .models import ALLOWED_ARTIFACT_NAMES


class ArtifactStoreError(RuntimeError):
    """Base class for artifact storage failures."""


class UnsafeArtifactPathError(ArtifactStoreError):
    """Raised when a run or artifact selector could escape the configured root."""


class InvalidArtifactError(ArtifactStoreError):
    """Raised when artifact content does not match the filename contract."""


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    name: str
    relative_path: str
    sha256: str
    row_count: int | None
    size_bytes: int


def _normalize_run_id(value: str | UUID) -> str:
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError) as exc:
        raise UnsafeArtifactPathError("run_id must be a valid UUID") from exc


class ArtifactStore:
    """Write and read only allowlisted files beneath ``root/<run UUID>/``."""

    def __init__(self, root: str | Path) -> None:
        configured = Path(root).expanduser()
        configured.mkdir(parents=True, exist_ok=True)
        self.root = configured.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("artifact root must be a directory")

    def path_for(self, run_id: str | UUID, name: str) -> Path:
        normalized_run_id = _normalize_run_id(run_id)
        self._validate_name(name)
        run_directory = (self.root / normalized_run_id).resolve(strict=False)
        if not run_directory.is_relative_to(self.root):
            raise UnsafeArtifactPathError("run directory escapes the artifact root")
        candidate = (run_directory / name).resolve(strict=False)
        if candidate.parent != run_directory or not candidate.is_relative_to(self.root):
            raise UnsafeArtifactPathError("artifact path escapes the run directory")
        return candidate

    def write_bytes(self, run_id: str | UUID, name: str, data: bytes) -> StoredArtifact:
        if not isinstance(data, bytes):
            raise TypeError("artifact data must be bytes")
        normalized_run_id = _normalize_run_id(run_id)
        path = self.path_for(normalized_run_id, name)
        row_count = self._csv_row_count(data) if name.endswith(".csv") else None
        digest = hashlib.sha256(data).hexdigest()
        if path.exists():
            existing = path.read_bytes()
            if existing != data:
                raise InvalidArtifactError(
                    "an artifact name cannot be reused with different content"
                )
            return StoredArtifact(
                name=name,
                relative_path=f"{normalized_run_id}/{name}",
                sha256=digest,
                row_count=row_count,
                size_bytes=len(data),
            )
        path.parent.mkdir(parents=True, exist_ok=True)

        file_descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        except Exception:
            try:
                os.close(file_descriptor)
            except OSError:
                pass
            temporary_path.unlink(missing_ok=True)
            raise

        return StoredArtifact(
            name=name,
            relative_path=f"{normalized_run_id}/{name}",
            sha256=digest,
            row_count=row_count,
            size_bytes=len(data),
        )

    def write_text(
        self,
        run_id: str | UUID,
        name: str,
        text: str,
        *,
        encoding: str = "utf-8",
    ) -> StoredArtifact:
        if not isinstance(text, str):
            raise TypeError("artifact text must be a string")
        return self.write_bytes(run_id, name, text.encode(encoding))

    def write_json(
        self, run_id: str | UUID, value: Mapping[str, Any], *, name: str = "audit.json"
    ) -> StoredArtifact:
        if not isinstance(value, Mapping):
            raise InvalidArtifactError("JSON artifact must be an object")
        return self.write_text(run_id, name, json_dumps(dict(value)) + "\n")

    def write_csv(
        self,
        run_id: str | UUID,
        name: str,
        fieldnames: Iterable[str],
        rows: Iterable[Mapping[str, Any]],
    ) -> StoredArtifact:
        if not name.endswith(".csv"):
            raise InvalidArtifactError("write_csv requires an allowlisted CSV filename")
        normalized_fields = tuple(fieldnames)
        if (
            not normalized_fields
            or any(not isinstance(field, str) or not field for field in normalized_fields)
            or len(set(normalized_fields)) != len(normalized_fields)
        ):
            raise InvalidArtifactError("CSV fieldnames must be non-empty and unique")
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(
            buffer,
            fieldnames=normalized_fields,
            extrasaction="raise",
            lineterminator="\n",
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(dict(row))
        return self.write_bytes(run_id, name, buffer.getvalue().encode("utf-8"))

    def read_bytes(self, run_id: str | UUID, name: str) -> bytes:
        path = self.path_for(run_id, name)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactStoreError(f"artifact does not exist: {name}") from exc

    def verify(self, run_id: str | UUID, artifact: StoredArtifact) -> bool:
        data = self.read_bytes(run_id, artifact.name)
        if len(data) != artifact.size_bytes:
            return False
        if hashlib.sha256(data).hexdigest() != artifact.sha256:
            return False
        if artifact.name.endswith(".csv"):
            return self._csv_row_count(data) == artifact.row_count
        return artifact.row_count is None

    @staticmethod
    def _validate_name(name: str) -> None:
        if name not in ALLOWED_ARTIFACT_NAMES:
            raise UnsafeArtifactPathError(f"artifact name is not allowed: {name!r}")

    @staticmethod
    def _csv_row_count(data: bytes) -> int:
        try:
            decoded = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidArtifactError("CSV artifacts must be UTF-8 encoded") from exc
        try:
            reader = csv.reader(io.StringIO(decoded, newline=""))
            header = next(reader)
        except (StopIteration, csv.Error) as exc:
            raise InvalidArtifactError("CSV artifact must contain a header") from exc
        if not header or any(not column for column in header):
            raise InvalidArtifactError("CSV artifact header contains an empty column")
        try:
            return sum(1 for _ in reader)
        except csv.Error as exc:
            raise InvalidArtifactError("CSV artifact is malformed") from exc
