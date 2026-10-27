"""Owned, append-only job scoring revisions. Reads never create or rescore runs."""
from copy import deepcopy
from typing import Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.assessments import InterviewScoringRevision
from app.services.recruitment_access import lock_job, owned_job
from app.services.interviews.analysis import digest
from app.services.interviews.performance_rubric import DEFAULT_POLICY, Policy, RUBRIC_VERSION


class SaveSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: Annotated[StrictInt, Field(ge=0)]
    policy: Policy


async def snapshot(db, job_id):
    row = await db.scalar(select(InterviewScoringRevision).where(
        InterviewScoringRevision.job_id == job_id).order_by(InterviewScoringRevision.revision.desc()).limit(1))
    policy = deepcopy(row.policy if row else DEFAULT_POLICY)
    return {"revision": row.revision if row else 0, "policy": policy,
            "policy_sha256": digest(policy), "rubric_version": RUBRIC_VERSION,
            "updated_at": row.created_at.isoformat() if row else None,
            "author_id": str(row.recruiter_id) if row else None}


async def get(job_id, recruiter_id):
    async with SessionFactory() as db:
        job = await owned_job(db, job_id, recruiter_id)
        return {**await snapshot(db, job_id), "default_policy": deepcopy(DEFAULT_POLICY),
                "job": {"id": str(job.id), "title": job.title, "requirements": job.requirements}}


async def save(job_id, recruiter_id, request):
    async with SessionFactory() as db:
        await owned_job(db, job_id, recruiter_id)
        await lock_job(db, job_id)
        current = await snapshot(db, job_id)
        if request.expected_revision != current["revision"]:
            raise HTTPException(409, "Scoring settings changed. Reload the settings before saving your changes.")
        policy = request.policy.model_dump()
        if policy != current["policy"]:
            db.add(InterviewScoringRevision(job_id=job_id, recruiter_id=recruiter_id,
                revision=current["revision"] + 1, policy=policy))
            await db.flush()
        result = await snapshot(db, job_id)
        await db.commit()
        return result
