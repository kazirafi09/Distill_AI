"""Synthetic document scope, integrity and result contracts; no graph execution."""
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from app.services.cv.processing import ParsedSource
from app.services.documents.extraction import MAX_FILE_BYTES


@dataclass(frozen=True)
class SyntheticScope:
    owner_id: UUID
    job_id: UUID
    run_id: UUID


@dataclass(frozen=True)
class SyntheticDocument:
    scope: SyntheticScope
    id: UUID
    file_sha256: str
    suffix: Literal[".pdf", ".docx"]

    def path(self, root: Path, expected_scope: SyntheticScope) -> Path:
        if self.scope != expected_scope:
            raise ValueError("SYNTHETIC_SOURCE_SCOPE_INVALID")
        if self.suffix not in {".pdf", ".docx"}:
            raise ValueError("SYNTHETIC_SOURCE_TYPE_INVALID")
        if len(self.file_sha256) != 64 or any(c not in "0123456789abcdef" for c in self.file_sha256):
            raise ValueError("SYNTHETIC_SOURCE_HASH_INVALID")
        base = root.resolve()
        directory = base / str(self.scope.owner_id) / str(self.scope.job_id) / str(self.scope.run_id)
        path = (directory / f"{self.id}{self.suffix}").resolve()
        if not path.is_relative_to(base) or not path.is_relative_to(directory):
            raise ValueError("SYNTHETIC_SOURCE_PATH_INVALID")
        return path

    def verify(self, root: Path, expected_scope: SyntheticScope) -> Path:
        path = self.path(root, expected_scope)
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE_BYTES + 1)
        if not 0 < len(data) <= MAX_FILE_BYTES or sha256(data).hexdigest() != self.file_sha256:
            raise ValueError("SYNTHETIC_DOCUMENT_INTEGRITY_FAILED")
        return path


@dataclass
class SyntheticAssessment:
    parsed: ParsedSource
    id: UUID = field(default_factory=uuid4)
    status: str = "pending"
    matches: list[dict] = field(default_factory=list)
    score: dict | None = None
    warnings: list[str] = field(default_factory=list)
    error_code: str | None = None
