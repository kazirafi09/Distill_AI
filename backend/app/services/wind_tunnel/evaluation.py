"""Deterministic, descriptive diagnostics; no model calls or JD edits."""
from copy import deepcopy
from decimal import Decimal
from itertools import combinations, permutations
from statistics import median

from app.services.cv.scoring import calculate_scores, rank_assessments, validate_scoring_profile
from app.services.wind_tunnel import storage
from app.services.wind_tunnel.generation import PERSONAS

EVALUATOR_VERSION = "synthetic-evaluation-2"


def rank(items, profile, rubric):
    # Shared utility uses application_id as a sort key; these are document IDs,
    # never real application records. Ranking/credit/tie rules are unchanged.
    ranked = rank_assessments([{"application_id": item["document_id"], "persona": item["persona"],
        "assessment_id": item["assessment_id"], "score": calculate_scores(profile, item["matches"], rubric)} for item in items])
    return [{"persona": item["persona"], "assessment_id": item["assessment_id"],
             "score": item["score"]["aggregate"], "rank": item["rank"]} for item in ranked]


def expectation(outcomes, expected_count=3):
    if len(outcomes) != expected_count or len(set(outcomes)) != 1:
        return "inconclusive"
    return "expected" if outcomes[0] else "unexpected"


def evaluate(snapshot, plan, assessments, *, repetitions=1):
    if type(repetitions) is not int or repetitions not in {1, 3}:
        raise ValueError("SYNTHETIC_REPETITIONS_INVALID")
    passes = range(1, repetitions + 1)
    profile = validate_scoring_profile(snapshot["screening_profile"], snapshot["profile_version"])
    rubric, criteria = snapshot["rubric"], profile["criteria"]
    pairs = [(a["persona"], a["repetition"]) for a in assessments]
    if len(set(pairs)) != len(pairs) or any(p not in PERSONAS or r not in passes for p, r in pairs):
        raise ValueError("SYNTHETIC_COHORT_IDENTITY_INVALID")
    completed = [a for a in assessments if a["status"] == "completed"]
    for item in completed:
        if item["score"] != calculate_scores(profile, item["matches"], rubric):
            raise ValueError("SYNTHETIC_SCORE_INTEGRITY_FAILED")
    cohorts = []
    for repetition in passes:
        items = [a for a in completed if a["repetition"] == repetition]
        missing = [p for p in PERSONAS if not any(a["persona"] == p for a in items)]
        cohorts.append({"repetition": repetition, "status": "inconclusive" if missing else "complete",
                        "missing_personas": missing, "ranks": [] if missing else rank(items, profile, rubric)})
    complete_cohorts = [c for c in cohorts if c["status"] == "complete"]
    target = plan["personas"]["near_miss"]
    findings, observations, summaries = [], [], {}
    for persona in PERSONAS:
        items = [a for a in completed if a["persona"] == persona]
        scores = [Decimal(a["score"]["aggregate"]) for a in items]
        ranked = [next(r for r in c["ranks"] if r["persona"] == persona) for c in complete_cohorts]
        top_outcomes = [r["rank"] == 1 for r in ranked]
        summaries[persona] = {"completed_passes": len(items), "scores": [str(v) for v in scores],
            "median_score": str(median(scores)) if scores else None,
            "score_range": str(max(scores) - min(scores)) if scores and repetitions > 1 else None,
            "complete_cohort_ranks": [r["rank"] for r in ranked],
            "variation_observed": (len(set(scores)) > 1 or len({r["rank"] for r in ranked}) > 1) if repetitions > 1 else None,
            "strong_top_expectation": expectation(top_outcomes, repetitions) if persona == "strong_all_rounder" else "not_applicable",
            "must_have_review_flags": [{"repetition": a["repetition"], "criterion_ids": a["score"]["must_have_review_flags"]} for a in items]}
        for criterion in criteria:
            matches = [(a, next(m for m in a["matches"] if m["requirement_id"] == criterion["id"])) for a in items]
            decisions = [m["decision"] for _, m in matches]
            missing_probe = persona == "near_miss" and criterion["id"] == target["criterion_id"]
            outcomes = [d != "met" if missing_probe else d == "met" for d in decisions]
            status = ("observed" if len(decisions) == repetitions and len(set(decisions)) == 1 else "inconclusive") if persona == "keyword_stuffer" else expectation(outcomes, repetitions)
            if len(set(decisions)) > 1:
                status = "inconclusive"
            identifier = "criterion-" + storage.digest([persona, criterion["id"],
                [(a["assessment_id"], m["decision"]) for a, m in matches]])[:24]
            observations.append({"finding_id": identifier if matches else None, "persona": persona,
                "criterion_id": criterion["id"], "decisions": decisions, "status": status,
                "essential_gap_applicable": target["essential_gap_applicable"] if missing_probe else None,
                "review_flag_expectation": expectation([criterion["id"] in a["score"]["must_have_review_flags"] for a, _ in matches], repetitions)
                    if missing_probe and target["review_flag_expected"] else "not_applicable"})
            if matches:
                findings.append(storage.StoredFinding(id=identifier,
                    summary=f"{persona}: {len(matches)}/{repetitions} criterion observations; {status}. Decisions: {', '.join(decisions)}. System diagnostic assumption, not proof of a JD defect.",
                    references=[storage.EvidenceReference(assessment_id=a["assessment_id"], requirement_id=criterion["id"]) for a, _ in matches]))
    comparisons = []
    for left, right in combinations(PERSONAS, 2):
        gaps = []
        for cohort in complete_cohorts:
            by_persona = {r["persona"]: r for r in cohort["ranks"]}
            gaps.append(Decimal(by_persona[left]["score"]) - Decimal(by_persona[right]["score"]))
        signs = {(g > 0) - (g < 0) for g in gaps}
        comparisons.append({"left": left, "right": right, "complete_cohorts": len(gaps),
            "score_gaps": [str(g) for g in gaps], "wins": sum(g > 0 for g in gaps),
            "ties": sum(g == 0 for g in gaps), "losses": sum(g < 0 for g in gaps),
            "status": "descriptive" if len(gaps) == repetitions and len(signs) == 1 else "inconclusive"})
    # Fixed-match weight sensitivity, not a claim about rewriting criterion text.
    active = [d for d, w in profile["dimension_weights"].items() if w > 0]
    vectors = []
    for donor, recipient in permutations(active, 2):
        weights = {d: Decimal(str(w)) for d, w in profile["dimension_weights"].items()}
        if weights[donor] < 5 or weights[recipient] > 95:
            continue
        weights[donor] -= 5
        weights[recipient] += 5
        modified = deepcopy(profile)
        modified["dimension_weights"] = {d: float(w) for d, w in weights.items()}
        try:
            validate_scoring_profile(modified, snapshot["profile_version"])
        except ValueError:
            continue
        results = []
        for cohort in complete_cohorts:
            ranked = rank([a for a in completed if a["repetition"] == cohort["repetition"]], modified, rubric)
            before = sorted(r["persona"] for r in cohort["ranks"] if r["rank"] == 1)
            after = sorted(r["persona"] for r in ranked if r["rank"] == 1)
            results.append({"repetition": cohort["repetition"], "ranks": ranked,
                            "baseline_top": before, "top": after, "top_set_changed": before != after})
        vectors.append({"donor": donor, "recipient": recipient, "weights": {d: str(w) for d, w in weights.items()}, "cohorts": results})
    return {"version": EVALUATOR_VERSION, "kind": "system_diagnostic_assumptions",
        "status": "completed" if len(completed) == len(PERSONAS) * repetitions else "completed_with_issues",
        "coverage": {"expected_assessments": len(PERSONAS) * repetitions, "completed_assessments": len(completed), "complete_cohorts": len(complete_cohorts)},
        "cohorts": cohorts, "personas": summaries, "criterion_observations": observations, "comparisons": comparisons,
        "weight_sensitivity": {"conditional_on_fixed_matches": True, "transfer_points": 5, "tested_vectors": vectors,
            "finding_ids": [f.id for f in findings]},
        "limitations": (["One assessment per CV; repeatability and variation have not been measured."] if repetitions == 1 else []) + ["Synthetic observations do not establish statistical confidence.",
            "Zero observed variation does not establish determinism.",
            "Unexpected results do not prove a JD defect or that an edit would improve matching.",
            "Incomplete cohorts have no ranks; inconsistent or missing observations are inconclusive."]}, findings
