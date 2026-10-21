"""Versioned interview performance job policy and deterministic, evidence-gated score arithmetic."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

Dimension = Literal["technical_competency", "communication", "response_relevance",
                    "problem_solving", "experience_depth", "job_alignment"]
DIMENSIONS = ("technical_competency", "communication", "response_relevance",
              "problem_solving", "experience_depth", "job_alignment")
RUBRIC_VERSION = "interview-performance-1"


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    weights: dict[Dimension, Annotated[StrictInt, Field(ge=0, le=100)]]
    minimum_assessed_dimensions: Annotated[StrictInt, Field(ge=1, le=6)] = 4
    essential_dimensions: list[Dimension] = Field(default_factory=lambda: ["technical_competency"], max_length=6)

    @model_validator(mode="after")
    def valid_gate(self):
        if set(self.weights) != set(DIMENSIONS) or sum(self.weights.values()) != 100:
            raise ValueError("Provide all six weights, totalling exactly 100%.")
        active = {name for name, weight in self.weights.items() if weight > 0}
        if (len(set(self.essential_dimensions)) != len(self.essential_dimensions)
                or not set(self.essential_dimensions) <= active
                or len(self.essential_dimensions) > self.minimum_assessed_dimensions
                or self.minimum_assessed_dimensions > len(active)):
            raise ValueError("Coverage must fit positively weighted dimensions; essentials must be unique and weighted.")
        return self


DEFAULT_POLICY = Policy(weights=dict(zip(DIMENSIONS, (25, 15, 10, 20, 20, 10), strict=True))).model_dump()

# Shared scale, with dimension-specific interpretation supplied to the evaluator.
ANCHORS = {
    "0": "Observed relevant response demonstrates a fundamental error or no usable response to an explicitly asked question.",
    "1": "Limited demonstrated competence; substantial errors, missing reasoning or interviewer support.",
    "2": "Partially adequate demonstration; important gaps or unexamined assumptions remain.",
    "3": "Sound, relevant demonstration with concrete reasoning; minor gaps only.",
    "4": "Strong, detailed demonstration with justified trade-offs, limitations and independent reasoning.",
    "null": "No suitable candidate evidence, unclear attribution, or insufficient reliable discussion; never treat missing evidence as zero.",
}
DIMENSION_GUIDANCE = {
    "technical_competency": "Technical correctness and depth using saved F5 accuracy evidence and eligible answer references; label model-only judgments.",
    "communication": "Clarity and organization of attributable text only; ignore accent, voice, fluency, disability, personality and presentation polish.",
    "response_relevance": "Whether answers address the questions actually asked; do not double-penalize missing detail.",
    "problem_solving": "Explicit decomposition, assumptions, alternatives, testing and reasoning. Null if no suitable problem-solving question was asked.",
    "experience_depth": "Concrete personal responsibility, decisions and operational examples; self-report is not external verification.",
    "job_alignment": "Demonstrated discussion of the saved job criteria; unasked competencies are coverage gaps, not automatic failure.",
}


def calculate(evaluations, policy):
    """Use validated 0–4 scores; exclude nulls, disclose all excluded weight."""
    policy = Policy.model_validate(policy)
    if set(evaluations) != set(DIMENSIONS):
        raise ValueError("INTERVIEW_PERFORMANCE_DIMENSIONS_INVALID")
    scores = {key: item["score"] for key, item in evaluations.items()}
    if any(value is not None and (type(value) is not int or not 0 <= value <= 4) for value in scores.values()):
        raise ValueError("INTERVIEW_PERFORMANCE_SCORE_INVALID")
    assessed = {key for key, value in scores.items() if value is not None and policy.weights[key] > 0}
    weight = sum(policy.weights[key] for key in assessed)
    eligible = (len(assessed) >= policy.minimum_assessed_dimensions
                and set(policy.essential_dimensions) <= assessed and weight > 0)
    overall = None
    if eligible:
        overall = str((sum(Decimal(policy.weights[key] * scores[key] * 25) for key in assessed)
                       / Decimal(weight)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    return {"overall_score_100": overall, "status": "insufficient_evidence" if not eligible else
            "provisional" if weight < 100 else "assessed", "assessed_dimensions": len(assessed),
            "assessed_weight": weight, "missing_weight": 100 - weight,
            "policy": policy.model_dump(), "rubric_version": RUBRIC_VERSION}
