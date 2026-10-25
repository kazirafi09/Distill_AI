"""Read saved, recruiter-owned jobs for chat without opening another workspace."""
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.jd import JDConversation, JobDescription
from app.services.jd.edit_policy import description_details


def job_summary(document):
    profile = document.screening_profile or {}
    criteria = profile.get("criteria")
    return {"id": str(document.id), "number": document.number, "title": document.title,
        "status": "approved", "created_at": document.created_at.isoformat(),
        "profile_version": document.profile_version,
        "essential_criteria": sum(c.get("priority") == "Must have" for c in criteria) if isinstance(criteria, list) else None,
        "preferred_criteria": sum(c.get("priority") == "Preferred" for c in criteria) if isinstance(criteria, list) else None,
        "screening_profile_available": document.screening_profile is not None}


async def find_owned_jobs(recruiter_id, *, job_number=None, title=None, limit=5, offset=0):
    if (type(limit) is not int or not 1 <= limit <= 10
        or type(offset) is not int or not 0 <= offset <= 1000
        or (job_number is not None and (type(job_number) is not int or not 1 <= job_number < 2**63))
        or (title is not None and (not isinstance(title, str) or not 1 <= len(title.strip()) <= 200))
        or (job_number is not None and title is not None)):
        return {"status": "invalid_request", "message": "Use a positive job number or a title (up to 200 characters), with a limit of 1–10."}
    async with SessionFactory() as db:
        query = select(JobDescription).join(JDConversation,
            JobDescription.conversation_id == JDConversation.id).where(JDConversation.recruiter_id == recruiter_id)
        if job_number is not None:
            query = query.where(JobDescription.number == job_number)
        if title is not None:
            query = query.where(JobDescription.title.icontains(title.strip(), autoescape=True))
        rows = list(await db.scalars(query.order_by(JobDescription.created_at.desc(), JobDescription.id.desc())
            .offset(offset).limit(limit + 1)))
        if not rows:
            return {"status": "not_found", "jobs": [], "has_more": False,
                    "message": "No saved jobs matching this request were found in your account."}
        more = len(rows) > limit
        jobs = [job_summary(document) for document in rows[:limit]]
        unique = (job_number is not None or title is not None) and offset == 0 and len(rows) == 1
        if unique:
            # Reuse the existing saved-JD projection; omit edit tokens, routing
            # destinations and conversation identifiers from the tool response.
            details = await description_details(db, rows[0])
            jobs[0].update({key: details[key] for key in (
                "content", "screening_profile", "google_form_url", "posting_status",
            )})
        return {"status": "found" if unique else "matches", "jobs": jobs,
                "has_more": more, "next_offset": offset + limit if more else None}
