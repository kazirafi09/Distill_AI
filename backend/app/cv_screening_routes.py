from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends
from app.auth import require_recruiter
from app.models.user import User
from app.services.cv.screening import get_screening_results, resume_run, run_status, start_job_screening

router = APIRouter(prefix="/cv/screening", tags=["CV Screening"])
Recruiter = Annotated[User, Depends(require_recruiter)]


@router.post("/jobs/{job_id}/start", status_code=202)
async def start(job_id: UUID, recruiter: Recruiter):
    return await start_job_screening(job_id, recruiter.id)


@router.post("/runs/{run_id}/resume", status_code=202)
async def resume(run_id: UUID, recruiter: Recruiter):
    return await resume_run(run_id, recruiter.id)


@router.get("/runs/{run_id}")
async def status(run_id: UUID, recruiter: Recruiter):
    return await run_status(run_id, recruiter.id)


@router.get("/jobs/{job_id}/results")
async def results(job_id: UUID, recruiter: Recruiter):
    return await get_screening_results(job_id, recruiter.id)
