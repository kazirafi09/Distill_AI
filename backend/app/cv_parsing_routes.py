from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.auth import require_recruiter
from app.models.user import User
from app.services.cv.parsing import get_cv_profile, parse_cv, parse_job

router = APIRouter(prefix="/cv", tags=["CV Parsing"])
Recruiter = Annotated[User, Depends(require_recruiter)]


@router.post("/versions/{file_version_id}/parse")
async def parse_version(file_version_id: UUID, recruiter: Recruiter):
    return await parse_cv(file_version_id, recruiter.id)


@router.get("/versions/{file_version_id}/profile")
async def profile(file_version_id: UUID, recruiter: Recruiter):
    return await get_cv_profile(file_version_id, recruiter.id)


@router.post("/jobs/{job_id}/parse")
async def batch(job_id: UUID, recruiter: Recruiter,
                offset: Annotated[int, Query(ge=0)] = 0,
                limit: Annotated[int, Query(ge=1, le=20)] = 20):
    return await parse_job(job_id, recruiter.id, offset, limit)
