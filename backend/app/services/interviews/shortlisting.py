"""Preview interviewees using existing screening results."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.cv import Candidate, CandidateApplication
from app.services.cv.indexing import digest
from app.services.cv.screening import get_screening_results, valid_aggregate_score


class ShortlistRequest(BaseModel):
    mode: Literal["top_k", "minimum_score", "both"]
    k: int | None = Field(default=None, strict=True, gt=0)
    minimum_score: Decimal | None = Field(
        default=None,
        ge=0,
        le=100,
        allow_inf_nan=False,
    )

    @model_validator(mode="after")
    def validate_filter_inputs(self):
        if self.mode in {"top_k", "both"} and self.k is None:
            raise ValueError("K is required for this filter.")

        if self.mode in {"minimum_score", "both"}:
            if self.minimum_score is None:
                raise ValueError("Minimum score is required for this filter.")

        return self


async def preview_shortlist(job_id, recruiter_id, filters: ShortlistRequest, *, db=None):
    if db is None:
        async with SessionFactory() as session:
            response = await preview_shortlist(job_id, recruiter_id, filters, db=session)
            await session.commit()
            return response
    # Existing code checks ownership, freshness, and ranking.
    results = await get_screening_results(job_id, recruiter_id, db=db)
    roster = (await db.execute(
        select(CandidateApplication, Candidate).join(Candidate).where(
            CandidateApplication.job_description_id == job_id,
            Candidate.recruiter_id == recruiter_id,
        ).order_by(CandidateApplication.id)
    )).all()
    qualifying = results["ranked"]

    if filters.mode in {"minimum_score", "both"}:
        qualifying = [
            application
            for application in qualifying
            if Decimal(application["score"]["aggregate"]) >= filters.minimum_score
        ]

    proposed = qualifying
    if filters.mode in {"top_k", "both"}:
        proposed = qualifying[:filters.k]

    proposed_ids = {application["application_id"] for application in proposed}
    qualifying_ids = {
        application["application_id"] for application in qualifying
    }

    excluded = []
    for application in results["ranked"]:
        if application["application_id"] in proposed_ids:
            continue

        reason = "outside_top_k"
        if application["application_id"] not in qualifying_ids:
            reason = "below_minimum_score"

        excluded.append({**application, "exclusion_reason": reason})

    for application in results["incomplete"]:
        if application["outdated"]:
            reason = "outdated_assessment"
        elif application["status"] != "completed":
            reason = "assessment_not_completed"
        elif not isinstance(application.get("score"), dict) or application["score"].get("aggregate") is None:
            reason = "score_missing"
        elif not valid_aggregate_score(application["score"]):
            reason = "score_invalid"
        else:
            reason = "current_ranking_unavailable"

        excluded.append({**application, "exclusion_reason": reason})

    assessed_ids = {item["application_id"] for item in results["ranked"] + results["incomplete"]}
    application_roster = []
    for application, candidate in roster:
        application_id = str(application.id)
        application_roster.append({"application_id": application_id,
                                   "name": candidate.full_name, "email": candidate.email})
        if application_id not in assessed_ids:
            excluded.append({
                "id": application_id, "application_id": application_id,
                "name": candidate.full_name, "status": "not_assessed", "outdated": False,
                "score": None, "matches": [], "warnings": [], "error_code": None,
                "exclusion_reason": "assessment_missing",
            })

    filter_inputs = filters.model_dump(mode="json")

    preview_key = digest({
        "job_id": results["job_id"],
        "recruiter_id": str(recruiter_id),
        "run": results["run"],
        "filters": filter_inputs,
        "application_roster": application_roster,
        "ranked": results["ranked"],
        "incomplete": sorted(
            results["incomplete"],
            key=lambda application: application["application_id"],
        ),
        "warnings": results["warnings"],
    })

    return {
        "job_id": results["job_id"],
        "run": results["run"],
        "filters": filter_inputs,
        "preview_key": preview_key,
        "proposed": proposed,
        "proposed_count": len(proposed),
        "excluded": excluded,
        "warnings": results["warnings"],
    }
