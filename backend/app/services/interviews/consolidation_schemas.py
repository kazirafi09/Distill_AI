"""F6 model output has findings/citations only. Source scores are server-owned."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Finding(Contract):
    criterion_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=1200)
    source_ids: list[str] = Field(min_length=1, max_length=8)


class Findings(Contract):
    findings: list[Finding] = Field(max_length=8)


class Suitability(Contract):
    position: Literal["supported_for_further_consideration", "mixed_evidence", "insufficient_evidence", "evidence_raises_concerns"]
    rationale: str = Field(min_length=1, max_length=1500)
    source_ids: list[str] = Field(min_length=1, max_length=12)


class AssessmentDraft(Contract):
    strengths: list[Finding] = Field(max_length=8)
    weaknesses: list[Finding] = Field(max_length=8)
    evidence_gaps: list[Finding] = Field(max_length=8)
    recommended_areas: list[Finding] = Field(max_length=8)
    suitability: Suitability
