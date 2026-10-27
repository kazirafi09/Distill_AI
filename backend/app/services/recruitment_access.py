"""Shared ownership checks and short job-write serialization, without model imports."""
from fastapi import HTTPException
from sqlalchemy import select, text
from app.models.cv import Candidate, CandidateApplication
from app.models.jd import JDConversation, JobDescription


async def lock_job(db, job_id):
    with db.no_autoflush:
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": job_id.int % (2**63 - 1)})


async def owned_job(db, job_id, recruiter_id):
    job = await db.scalar(select(JobDescription).join(JDConversation).where(JobDescription.id == job_id,
        JDConversation.recruiter_id == recruiter_id))
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


async def owned_application(db, job_id, application_id, recruiter_id):
    await owned_job(db, job_id, recruiter_id)
    application = await db.scalar(select(CandidateApplication).join(Candidate).where(CandidateApplication.id == application_id,
        CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id))
    if not application:
        raise HTTPException(404, "Application not found.")
    return application
