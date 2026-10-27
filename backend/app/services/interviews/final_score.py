"""Read-only combined score projection; original assessment artifacts stay immutable."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy import select

from app.models.assessments import AssessmentRun, AssessmentResult
from app.services.interviews.analysis import digest
from app.services.interviews.post_interview_analysis import source_warnings

POLICY = {"version": "candidate-final-1", "cv_weight": 30, "interview_weight": 70,
          "minimum_assessed_weight": 80, "minimum_assessed_dimensions": 4}


def score(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and 0 <= number <= 100 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def display(value):
    return str(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def combine(cv, performance, warnings=()):
    result = {"policy": POLICY, "status": "incomplete", "score_100": None,
              "cv_score_100": None, "interview_score_100": None, "warnings": list(warnings)}
    cv_score = score((cv or {}).get("aggregate"))
    if cv_score is not None:
        result["cv_score_100"] = display(cv_score)
    summary = (performance or {}).get("summary", {})
    interview_score = score(summary.get("overall_score_100"))
    if interview_score is not None:
        result["interview_score_100"] = display(interview_score)
    if cv_score is None or interview_score is None:
        return {**result, "reason": "A saved CV score and completed interview performance score are both required."}
    essentials = summary.get("policy", {}).get("essential_dimensions", ["technical_competency"])
    dimensions = (performance or {}).get("dimensions", {})
    if (summary.get("assessed_weight", 0) < POLICY["minimum_assessed_weight"]
            or summary.get("assessed_dimensions", 0) < POLICY["minimum_assessed_dimensions"]
            or any(dimensions.get(name, {}).get("score") is None for name in essentials)
            or dimensions.get("technical_competency", {}).get("score") is None):
        return {**result, "reason": "Interview evidence must cover at least 80% of weight, four dimensions and essential technical competency."}
    cv_points = cv_score * Decimal(POLICY["cv_weight"]) / 100
    interview_points = interview_score * Decimal(POLICY["interview_weight"]) / 100
    if cv.get("must_have_review_flags"):
        result["warnings"].append("One or more essential CV requirements need review.")
    return {**result, "status": "provisional", "reason": "For recruiter review; not an automatic hiring decision.",
            "score_100": display(cv_points + interview_points), "cv_contribution": display(cv_points),
            "interview_contribution": display(interview_points)}


async def for_run(db, run, payload):
    cv = (run.inputs.get("cv") or {}).get("score") if run.kind == "f6" else (run.inputs.get("cv_snapshot") or {}).get("score")
    warnings = source_warnings(run.inputs.get("prepared", {}))
    if (run.status != "completed" or not payload or payload.get("artifact_sha256") !=
            digest({key: value for key, value in payload.items() if key != "artifact_sha256"})):
        return combine(cv, None, warnings)
    parent_id = run.parent_run_id if run.kind == "f6" else run.id
    latest = await db.scalar(select(AssessmentRun).where(
        AssessmentRun.parent_run_id == parent_id, AssessmentRun.kind == "interview_performance",
        AssessmentRun.recruiter_id == run.recruiter_id, AssessmentRun.job_id == run.job_id,
        AssessmentRun.application_id == run.application_id, AssessmentRun.synthetic == run.synthetic,
    ).order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc()).limit(1))
    saved = await db.get(AssessmentResult, latest.id) if latest and latest.status == "completed" else None
    performance = saved.payload if saved else None
    f5_hash = run.inputs.get("f5", {}).get("artifact_sha256") if run.kind == "f6" else payload.get("artifact_sha256")
    if performance and (performance.get("artifact_sha256") != digest({key: value for key, value in performance.items() if key != "artifact_sha256"})
                        or performance.get("provenance", {}).get("f5_artifact_sha256") != f5_hash):
        performance = None
    result = combine(cv, performance, warnings)
    if latest:
        result.update(interview_run_id=str(latest.id), interview_created_at=latest.created_at.isoformat(),
                      interview_status=latest.status)
    return result
