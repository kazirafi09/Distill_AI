"""Verbatim claim values deliberately avoid inferred proficiency or experience."""
from typing import Any
import re
from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "cv-profile-1"


class CVParsingResponse(BaseModel):
    """Public parsing result contract, including MCP structured output."""
    model_config = ConfigDict(extra="forbid")
    file_version_id: str
    status: str
    id: str | None = None
    attempt: int | None = None
    file_sha256: str | None = None
    versions: dict[str, Any] | None = None
    extraction_method: str | None = None
    ocr_used: bool = False
    warnings: list[str] = Field(default_factory=list)
    profile: dict[str, Any] | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    updated_at: str | None = None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Excerpt(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=2, max_length=1500)


class Claim(StrictModel):
    value: str = Field(min_length=1, max_length=1500)
    evidence: list[Excerpt] = Field(min_length=1, max_length=5)


class Contact(StrictModel):
    name: Claim | None = None
    email: Claim | None = None
    phone: Claim | None = None
    address: Claim | None = None
    links: list[Claim] = Field(default_factory=list, max_length=20)


class Education(StrictModel):
    institution: Claim | None = None
    qualification: Claim | None = None
    field: Claim | None = None
    dates: Claim | None = None
    grade: Claim | None = None


class Work(StrictModel):
    employer: Claim | None = None
    title: Claim | None = None
    dates: Claim | None = None
    stated_duration: Claim | None = None
    responsibilities: list[Claim] = Field(default_factory=list, max_length=40)


class Certification(StrictModel):
    name: Claim | None = None
    issuer: Claim | None = None
    dates: Claim | None = None


class Project(StrictModel):
    name: Claim | None = None
    description: Claim | None = None
    dates: Claim | None = None
    technologies: list[Claim] = Field(default_factory=list, max_length=30)


class CandidateProfile(StrictModel):
    contact: Contact = Field(default_factory=Contact)
    education: list[Education] = Field(default_factory=list, max_length=30)
    work_history: list[Work] = Field(default_factory=list, max_length=40)
    skills: list[Claim] = Field(default_factory=list, max_length=100)
    certifications: list[Certification] = Field(default_factory=list, max_length=40)
    projects: list[Project] = Field(default_factory=list, max_length=40)


def resolve_excerpt(segment: str, quote: str) -> tuple[int, str]:
    offset = segment.find(quote)
    if offset >= 0:
        return offset, quote
    # Models commonly collapse PDF layout spaces/newlines. Match only identical
    # non-whitespace characters, case-sensitively; then save the ORIGINAL text.
    # Ambiguous normalized matches fail rather than selecting an invented location.
    tokens = list(re.finditer(r"\S+", segment))
    canonical = " ".join(t.group() for t in tokens)
    wanted = " ".join(quote.split())
    position = canonical.find(wanted) if wanted else -1
    if position < 0 or canonical.find(wanted, position + 1) >= 0:
        raise ValueError("EVIDENCE_NOT_IN_TEXT")
    mapping = []
    for token in tokens:
        if mapping:
            mapping.append(token.start() - 1)
        mapping.extend(range(token.start(), token.end()))
    start, end = mapping[position], mapping[position + len(wanted) - 1] + 1
    return start, segment[start:end]


def validate_profile(payload: str | dict, text: str, sources: list) -> tuple[dict, list]:
    profile = (CandidateProfile.model_validate_json(payload) if isinstance(payload, str)
               else CandidateProfile.model_validate(payload)).model_dump()
    lookup = {source["id"]: source for source in sources}
    evidence = []

    def visit(value, path):
        if isinstance(value, dict) and "value" in value:
            supported = False
            for excerpt in value["evidence"]:
                source = lookup.get(excerpt["source_id"])
                if not source:
                    raise ValueError("UNKNOWN_EVIDENCE_SOURCE")
                segment = text[source["start"]:source["end"]]
                offset, excerpt["quote"] = resolve_excerpt(segment, excerpt["quote"])
                # Copy stated facts only. Case/spacing normalization is harmless;
                # transformed dates, durations and synthesized qualifications fail.
                normalized = lambda s: " ".join(s.casefold().split())
                supported |= normalized(value["value"]) in normalized(excerpt["quote"])
                evidence.append({"claim_path": path, **excerpt, "page": source["page"],
                                 "ocr_derived": source["method"] == "ocr",
                                 "start": source["start"] + offset,
                                 "end": source["start"] + offset + len(excerpt["quote"])})
            if not supported:
                raise ValueError("CLAIM_NOT_SUPPORTED")
        elif isinstance(value, dict):
            for key, item in value.items():
                visit(item, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}.{index}")

    visit(profile, "")
    if not evidence:
        raise ValueError("NO_SUPPORTED_CLAIMS")
    return profile, evidence


def identity_warnings(profile: dict, sheet_name: str | None, sheet_email: str | None) -> list[str]:
    warnings = []
    normalize = lambda s: " ".join(s.casefold().split())
    for field, stored in (("name", sheet_name), ("email", sheet_email)):
        claim = profile["contact"].get(field)
        if stored and claim and normalize(stored) != normalize(claim["value"]):
            warnings.append(f"CV_SHEET_{field.upper()}_DISCREPANCY")
    if not profile["contact"].get("name") or not profile["contact"].get("email"):
        warnings.append("CV_CONTACT_FIELDS_UNKNOWN")
    return warnings


def retain_supported_claims(payload: str, text: str, sources: list):
    """After bounded repair, retain supported facts and make others unknown.

    Malformed schemas cannot be salvaged. Each retained claim independently passes
    the full validator; an entirely unsupported profile still fails.
    """
    profile = CandidateProfile.model_validate_json(payload).model_dump()
    omitted = 0
    def visit(value):
        nonlocal omitted
        if isinstance(value, dict) and "value" in value:
            try:
                validated, _ = validate_profile({"skills": [value]}, text, sources)
                return validated["skills"][0]
            except ValueError:
                omitted += 1
                return None
        if isinstance(value, dict):
            return {key: visit(item) for key, item in value.items()}
        if isinstance(value, list):
            return [kept for item in value if (kept := visit(item)) is not None]
        return value
    retained = visit(profile)
    validated, evidence = validate_profile(retained, text, sources)
    return validated, evidence, omitted
