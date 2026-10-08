"""Initial recruiter-supplied rubric; documented support is not proven ability."""
from decimal import Decimal, localcontext
from app.services.screening_profiles import DIMENSIONS, ScreeningProfile

RUBRIC = {"version": "initial-1", "credit": {"met": "1", "partially_met": "0.5", "unmet": "0", "insufficient_evidence": "0"},
          "priority_weights": {"Must have": 2, "Preferred": 1}, "description": "Initial configurable rubric; not scientifically validated."}


def validate_scoring_profile(profile, version):
    parsed = ScreeningProfile.model_validate(profile)
    if not isinstance(version, int) or version < 1 or not parsed.weights_confirmed or not parsed.weights_confirmation_quote:
        raise ValueError("PROFILE_CORRECTION_NEEDED")
    weights = parsed.dimension_weights.model_dump()
    if any(not 0 <= Decimal(str(v)) <= 100 for v in weights.values()) or sum(Decimal(str(v)) for v in weights.values()) != 100:
        raise ValueError("PROFILE_WEIGHTS_INVALID")
    ids = [c.id for c in parsed.criteria]
    if not ids or len(ids) != len(set(ids)) or any(not v for v in ids) or any(c.priority not in RUBRIC["priority_weights"] for c in parsed.criteria):
        raise ValueError("PROFILE_CRITERIA_INVALID")
    if any(weights[d] > 0 and not any(c.dimension == d for c in parsed.criteria) for d in DIMENSIONS):
        raise ValueError("POSITIVE_DIMENSION_WITHOUT_CRITERIA")
    return parsed.model_dump(mode="json")


def calculate_scores(profile, matches, rubric=RUBRIC):
    if {m["requirement_id"] for m in matches} != {c["id"] for c in profile["criteria"]} or len(matches) != len(profile["criteria"]):
        raise ValueError("MATCH_COVERAGE_INVALID")
    by_id = {m["requirement_id"]: m for m in matches}
    contributions, dimensions, flags = [], [], []
    with localcontext() as ctx:
        ctx.prec = 50
        aggregate = Decimal(0)
        for dimension in DIMENSIONS:
            criteria = [c for c in profile["criteria"] if c["dimension"] == dimension]
            total, earned = Decimal(0), Decimal(0)
            justifications = []
            for criterion in criteria:
                match = by_id[criterion["id"]]
                weight = Decimal(str(rubric["priority_weights"][criterion["priority"]]))
                credit = Decimal(rubric["credit"][match["decision"]])
                total += weight
                earned += weight * credit
                contributions.append({"requirement_id": criterion["id"], "dimension": dimension,
                    "priority": criterion["priority"], "weight": str(weight), "credit": str(credit), "weighted_credit": str(weight * credit)})
                justifications.append({"requirement_id": criterion["id"], "justification": match["justification"]})
                if criterion["priority"] == "Must have" and match["decision"] != "met":
                    flags.append(criterion["id"])
            percentage = Decimal(str(profile["dimension_weights"][dimension]))
            if percentage > 0 and total == 0:
                raise ValueError("POSITIVE_DIMENSION_WITHOUT_CRITERIA")
            score = 100 * earned / total if total else Decimal(0)
            contribution = score * percentage / 100
            aggregate += contribution
            dimensions.append({"dimension": dimension, "score": str(score), "approved_percentage": str(percentage),
                "aggregate_contribution": str(contribution), "justifications": justifications})
        return {"aggregate": str(aggregate), "dimensions": dimensions, "contributions": contributions,
            "must_have_review_flags": flags, "evidence_coverage": str(Decimal(sum(bool(m["evidence"]) for m in matches)) / len(matches)),
            "rubric": rubric, "note": "Insufficient evidence earns zero documented credit; it is not proof of inability."}


def rank_assessments(items):
    # Negating a Decimal would apply the ambient precision and round its key.
    # Stable two-pass sorting compares the full saved value without arithmetic.
    ordered = sorted(items, key=lambda item: item["application_id"])
    ordered.sort(key=lambda item: Decimal(item["score"]["aggregate"]), reverse=True)
    previous, rank = None, 0
    for position, item in enumerate(ordered, 1):
        score = Decimal(item["score"]["aggregate"])
        if score != previous:
            rank = position
        item["rank"] = rank
        previous = score
    return ordered
