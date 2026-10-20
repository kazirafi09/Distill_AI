"""Small validated contracts for F5 model outputs, independent of graph state."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


Dimension = Literal["relevance", "accuracy", "completeness", "articulation"]
AnswerState = Literal["answered", "partially_answered", "explicitly_unanswered", "interrupted", "unclear"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class QuestionGroup(Contract):
    id: str = Field(pattern=r"^g[1-9][0-9]{0,2}$")
    topic: str = Field(min_length=1, max_length=300)
    question_turn_ids: list[str] = Field(min_length=1, max_length=100)
    answer_turn_ids: list[str] = Field(max_length=200)
    follow_up_turn_ids: list[str] = Field(default_factory=list, max_length=100)
    clarification_turn_ids: list[str] = Field(default_factory=list, max_length=100)
    revisit_turn_ids: list[str] = Field(default_factory=list, max_length=100)
    answer_state: AnswerState


class ExcludedTurn(Contract):
    turn_id: str
    reason: Literal["greeting", "social", "off_topic", "logistics", "aborted_prompt", "closing"]


class Grouping(Contract):
    groups: list[QuestionGroup] = Field(min_length=1, max_length=100)
    excluded_turns: list[ExcludedTurn] = Field(max_length=2000)


class GroupDescription(Contract):
    id: str = Field(pattern=r"^g[1-9][0-9]{0,2}$")
    topic: str = Field(min_length=1, max_length=300)
    answer_state: AnswerState


class TurnGrouping(Contract):
    """One ordered assignment per input turn; Python builds the source-ID lists."""

    groups: list[GroupDescription] = Field(min_length=1, max_length=100)
    assignments: list[str] = Field(min_length=1, max_length=1000)


class RequirementMapping(Contract):
    group_id: str
    criterion_ids: list[str] = Field(max_length=50)


class Mappings(Contract):
    mappings: list[RequirementMapping] = Field(min_length=1, max_length=100)


class TranscriptExcerpt(Contract):
    turn_id: str
    quote: str = Field(min_length=1, max_length=1500)


class ReferenceExcerpt(Contract):
    reference_id: str
    quote: str = Field(min_length=1, max_length=1500)


class DimensionEvaluation(Contract):
    dimension: Dimension
    score: int | None = Field(strict=True, ge=0, le=4)
    reason: Literal["ASSESSED", "EXPLICIT_NO_ANSWER", "NO_SUBSTANTIVE_ANSWER",
                    "TRANSCRIPT_UNCERTAIN", "NO_ACCURACY_BASIS", "SELF_REPORT_UNVERIFIED"]
    justification: str = Field(min_length=1, max_length=1500)
    evidence: list[TranscriptExcerpt] = Field(max_length=8)
    accuracy_basis: Literal["reference_supported", "model_knowledge", "mixed", "not_assessable", "not_applicable"]
    reference_evidence: list[ReferenceExcerpt] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def valid_result(self):
        if (self.score is None) != (self.reason != "ASSESSED"):
            raise ValueError("SCORE_REASON_CONFLICT")
        if self.score is not None and not self.evidence:
            raise ValueError("SCORED_ANSWER_EVIDENCE_REQUIRED")
        if self.dimension == "accuracy":
            if self.score is None and self.accuracy_basis != "not_assessable":
                raise ValueError("ACCURACY_BASIS_INVALID")
            if self.score is not None and self.accuracy_basis not in {"reference_supported", "model_knowledge", "mixed"}:
                raise ValueError("ACCURACY_BASIS_REQUIRED")
            if self.accuracy_basis in {"reference_supported", "mixed"} and not self.reference_evidence:
                raise ValueError("REFERENCE_EVIDENCE_REQUIRED")
            if self.accuracy_basis in {"model_knowledge", "not_assessable"} and self.reference_evidence:
                raise ValueError("REFERENCE_BASIS_CONFLICT")
        elif self.accuracy_basis != "not_applicable" or self.reference_evidence:
            raise ValueError("REFERENCE_BASIS_CONFLICT")
        return self
