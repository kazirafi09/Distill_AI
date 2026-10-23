"""F11 proposal contracts and read-only previews.

Callers must authorize the job and recheck its publication lock before saving.
Reusable for Wind Tunnel proposals. Structural validation does not establish
recruiter grounding or synthetic-test support. Wind Tunnel generation/storage
resolve proposals against owned runs, current findings and stored source evidence;
those references are separate from these reusable patch contracts.
"""
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.services.jd.conversations import extract_jd_title
from app.services.jd.edit_policy import document_edit_version
from app.services.jd.editing import EditableProfile, ManualEdit, editable_profile, reviewed_profile
from app.services.jd.screening_profiles import (
    DimensionWeights, contains_internal_metadata, public_jd_narrative, public_profile_section,
)

ANALYSIS_VERSION = "jd-suggestions-1"


class Contract(BaseModel):
    # Patch strings preserve exact whitespace for old-value comparisons.
    model_config = ConfigDict(extra="forbid")


class DescriptionPatch(Contract):
    kind: Literal["description"]
    expected: str = Field(min_length=1, max_length=40000)
    proposed: str = Field(min_length=1, max_length=40000)


class CriterionPatch(Contract):
    kind: Literal["criterion"]
    criterion_id: str = Field(min_length=1, max_length=100)
    field: Literal["text", "priority"]
    expected: str = Field(min_length=1, max_length=2000)
    proposed: str = Field(min_length=1, max_length=2000)


class WeightsPatch(Contract):
    kind: Literal["weights"]
    expected: DimensionWeights
    proposed: DimensionWeights


class MetadataPatch(Contract):
    kind: Literal["metadata"]
    field: Literal["minimum_years_experience", "fresher_eligibility"]
    expected: str | None = Field(max_length=1000)
    proposed: str | None = Field(max_length=1000)


Patch = Annotated[
    DescriptionPatch | CriterionPatch | WeightsPatch | MetadataPatch,
    Field(discriminator="kind"),
]


class SuggestionProposal(Contract):
    title: str = Field(min_length=1, max_length=100, pattern=r"\S")
    reason: str = Field(min_length=1, max_length=300, pattern=r"\S")
    patches: list[Patch] = Field(min_length=1, max_length=12)

    @property
    def affected_areas(self) -> list[str]:
        return sorted({"description" if p.kind == "description" else "criteria" for p in self.patches})


def preview_suggestion(document, proposal: SuggestionProposal, source_version: str) -> ManualEdit:
    """Check all old values against one saved version and return an unsaved edit.

Multi-area changes form one atomic proposal. Overlapping narrative patches and
repeated targets are rejected rather than making patch order affect the result.
No recruiter review, SQL mutation, or model request occurs here.
"""
    if source_version != document_edit_version(document):
        raise HTTPException(409, "This suggestion is stale. Request suggestions for the latest job.")
    if not document.screening_profile:
        raise HTTPException(422, "Screening criteria are required before suggesting changes.")
    narrative = public_jd_narrative(document.content)
    profile = editable_profile(document.screening_profile)
    replacements, targets = [], set()
    for patch in proposal.patches:
        if patch.expected == patch.proposed:
            raise HTTPException(422, "A suggestion must change its target value.")
        if isinstance(patch, DescriptionPatch):
            if narrative.count(patch.expected) != 1:
                raise HTTPException(422, "Suggested wording must identify exactly one passage.")
            start = narrative.index(patch.expected)
            end = start + len(patch.expected)
            if any(start < old_end and old_start < end for old_start, old_end, _ in replacements):
                raise HTTPException(422, "Suggested description changes overlap.")
            replacements.append((start, end, patch.proposed))
            continue
        if isinstance(patch, CriterionPatch):
            target = (patch.criterion_id, patch.field)
            criterion = next((c for c in profile["criteria"] if c["id"] == patch.criterion_id), None)
            if criterion is None or criterion[patch.field] != patch.expected:
                raise HTTPException(422, "Suggested criterion does not match the saved source.")
            if patch.field == "priority" and patch.proposed not in {"Must have", "Preferred"}:
                raise HTTPException(422, "Suggested priority must be Must have or Preferred.")
            criterion[patch.field] = patch.proposed
        elif isinstance(patch, WeightsPatch):
            target = ("profile", "dimension_weights")
            if profile["dimension_weights"] != patch.expected.model_dump():
                raise HTTPException(422, "Suggested weights do not match the saved source.")
            profile["dimension_weights"] = patch.proposed.model_dump()
        else:
            target = ("profile", patch.field)
            if profile[patch.field] != patch.expected:
                raise HTTPException(422, "Suggested metadata does not match the saved source.")
            profile[patch.field] = patch.proposed
        if target in targets:
            raise HTTPException(422, "A suggestion repeats a patch target.")
        targets.add(target)
    for start, end, proposed in sorted(replacements, reverse=True):
        narrative = narrative[:start] + proposed + narrative[end:]
    if narrative != public_jd_narrative(narrative):
        raise HTTPException(422, "Keep generated criteria sections out of description patches.")
    extract_jd_title(narrative)
    incoming = EditableProfile.model_validate(profile)
    checked = reviewed_profile(incoming, document.screening_profile)
    if contains_internal_metadata(narrative + "\n\n" + public_profile_section(checked)):
        raise HTTPException(422, "Keep internal screening metadata out of public wording.")
    return ManualEdit(edit_version=source_version, content=narrative, screening_profile=incoming)
