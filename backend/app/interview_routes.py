"""Recruiter endpoints for interviewee selection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import require_recruiter
from app.models.user import User
from app.services.interviews.shortlisting import (
    ShortlistRequest,
    preview_shortlist,
)

router = APIRouter(prefix="/interviews", tags=["Interviews"])
Recruiter = Annotated[User, Depends(require_recruiter)]


@router.post("/jobs/{job_id}/shortlist/preview")
async def shortlist_preview(
    job_id: UUID,
    filters: ShortlistRequest,
    recruiter: Recruiter,
):
    return await preview_shortlist(job_id, recruiter.id, filters)
