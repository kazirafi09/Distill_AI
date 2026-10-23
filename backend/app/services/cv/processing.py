"""Source contracts for the existing parsing and screening LangGraphs.

No alternative pipeline: workflow nodes own extraction, parsing and matching.
"""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Protocol
from uuid import UUID


class ParsedEvidenceSource(Protocol):
    """Implemented by CVParseResult and synthetic parsing records."""
    id: UUID
    file_version_id: UUID
    text: str
    sources: list[dict]
    profile: dict


@dataclass(frozen=True)
class SourceAccess:
    """Trusted entry adapters supply access checks and a scoped path resolver."""
    id: UUID
    path: Callable[[], Path]
    verify: Callable[[], object]


@dataclass
class ParsedSource:
    """Parsing record; source adapters own any persistence, not candidate ORM rows."""
    id: UUID
    file_version_id: UUID
    file_sha256: str
    versions: dict
    status: str = "pending"
    text: str | None = None
    sources: list[dict] = field(default_factory=list)
    profile: dict | None = None
    evidence: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extraction_method: str | None = None
    error_code: str | None = None
