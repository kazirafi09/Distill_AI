"""Save and load interviewee selections."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select, text

from app.db.session import SessionFactory
from app.models.cv import Candidate, CandidateApplication
from app.models.interviews import (
    InterviewShortlist,
    InterviewShortlistMember,
)
from app.services.cv.screening import (
    get_screening_results,
    owned_job,
)
from app.services.interviews.schemas import SaveShortlistRequest
from app.services.interviews.shortlisting import preview_shortlist


async def save_shortlist(
    job_id,
    recruiter_id,
    request: SaveShortlistRequest,
):
    async with SessionFactory() as db:
        await owned_job(db, job_id, recruiter_id)

        # Serialize saves for this JD using the existing screening lock.
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": job_id.int % (2**63 - 1)},
        )

        preview = await preview_shortlist(
            job_id,
            recruiter_id,
            request.filters,
        )

        if preview["preview_key"] != request.preview_key:
            raise HTTPException(
                409,
                "Screening results changed. Refresh the preview before saving.",
            )

        run = preview["run"]
        if run is None or run["status"] == "outdated" or preview["warnings"]:
            raise HTTPException(
                409,
                "Current screening results are required before saving.",
            )

        # Candidates outside the filter may still be manually selected.
        eligible = {
            item["application_id"]: item
            for item in preview["proposed"]
        }
        for item in preview["excluded"]:
            if item["exclusion_reason"] in {
                "outside_top_k",
                "below_minimum_score",
            }:
                eligible[item["application_id"]] = item

        selected_ids = set(request.selected_application_ids)

        if any(str(application_id) not in eligible for application_id in selected_ids):
            raise HTTPException(
                422,
                "Every selected application must have current eligible results.",
            )

        # Recheck job membership and recruiter ownership before writing.
        owned_ids = set(
            await db.scalars(
                select(CandidateApplication.id)
                .join(
                    Candidate,
                    CandidateApplication.candidate_id == Candidate.id,
                )
                .where(
                    CandidateApplication.id.in_(selected_ids),
                    CandidateApplication.job_description_id == job_id,
                    Candidate.recruiter_id == recruiter_id,
                )
            )
        )
        if owned_ids != selected_ids:
            raise HTTPException(
                422,
                "Selected applications must belong to this job and recruiter.",
            )

        shortlist = await db.scalar(
            select(InterviewShortlist).where(
                InterviewShortlist.job_description_id == job_id,
                InterviewShortlist.recruiter_id == recruiter_id,
            )
        )

        if shortlist is None:
            shortlist = InterviewShortlist(
                job_description_id=job_id,
                recruiter_id=recruiter_id,
            )
            db.add(shortlist)

        proposed_ids = {
            item["application_id"] for item in preview["proposed"]
        }

        shortlist.screening_run_id = UUID(run["id"])
        shortlist.filter_inputs = preview["filters"]
        shortlist.proposed_application_ids = sorted(proposed_ids)
        await db.flush()

        existing_members = {
            member.application_id: member
            for member in await db.scalars(
                select(InterviewShortlistMember).where(
                    InterviewShortlistMember.shortlist_id == shortlist.id,
                )
            )
        }

        # Remove candidates the recruiter unchecked.
        for application_id, member in existing_members.items():
            if application_id not in selected_ids:
                await db.delete(member)

        # Add new candidates or update retained candidates.
        for application_id in sorted(selected_ids):
            assessment = eligible[str(application_id)]
            member = existing_members.get(application_id)

            if member is None:
                member = InterviewShortlistMember(
                    shortlist_id=shortlist.id,
                    application_id=application_id,
                )
                db.add(member)

            member.assessment_id = UUID(assessment["id"])
            member.score_snapshot = assessment["score"]
            member.rank_snapshot = assessment["rank"]
            member.manually_added = str(application_id) not in proposed_ids

        await db.flush()

        # Check again before committing in case screening changed during saving.
        latest_preview = await preview_shortlist(
            job_id,
            recruiter_id,
            request.filters,
        )
        if latest_preview["preview_key"] != request.preview_key:
            raise HTTPException(
                409,
                "Screening results changed while saving. Refresh the preview.",
            )

        await db.commit()

    return await load_shortlist(job_id, recruiter_id)


async def load_shortlist(job_id, recruiter_id):
    async with SessionFactory() as db:
        await owned_job(db, job_id, recruiter_id)

        shortlist = await db.scalar(
            select(InterviewShortlist).where(
                InterviewShortlist.job_description_id == job_id,
                InterviewShortlist.recruiter_id == recruiter_id,
            )
        )
        if shortlist is None:
            return None

        rows = (
            await db.execute(
                select(InterviewShortlistMember, Candidate)
                .join(
                    CandidateApplication,
                    InterviewShortlistMember.application_id
                    == CandidateApplication.id,
                )
                .join(
                    Candidate,
                    CandidateApplication.candidate_id == Candidate.id,
                )
                .where(
                    InterviewShortlistMember.shortlist_id == shortlist.id,
                    CandidateApplication.job_description_id == job_id,
                    Candidate.recruiter_id == recruiter_id,
                )
                .order_by(
                    InterviewShortlistMember.rank_snapshot,
                    InterviewShortlistMember.application_id,
                )
            )
        ).all()

        results = await get_screening_results(job_id, recruiter_id)
        current_assessments = {
            item["application_id"]: item
            for item in results["ranked"]
        }

        current_run = results["run"]
        run_outdated = (
            current_run is None
            or current_run["id"] != str(shortlist.screening_run_id)
            or current_run["status"] == "outdated"
            or bool(results["warnings"])
        )

        members = []
        for member, candidate in rows:
            current = current_assessments.get(str(member.application_id))
            outdated = (
                run_outdated
                or current is None
                or current["id"] != str(member.assessment_id)
                or current["score"] != member.score_snapshot
                or current["rank"] != member.rank_snapshot
            )

            members.append({
                "application_id": str(member.application_id),
                "assessment_id": str(member.assessment_id),
                "name": candidate.full_name,
                "email": candidate.email,
                "score": member.score_snapshot,
                "rank": member.rank_snapshot,
                "manually_added": member.manually_added,
                "outdated": outdated,
            })

        return {
            "id": str(shortlist.id),
            "job_id": str(shortlist.job_description_id),
            "screening_run_id": str(shortlist.screening_run_id),
            "filters": shortlist.filter_inputs,
            "proposed_application_ids": shortlist.proposed_application_ids,
            "members": members,
            "selected_count": len(members),
            "outdated": run_outdated or any(
                member["outdated"] for member in members
            ),
            "warnings": results["warnings"],
        }
