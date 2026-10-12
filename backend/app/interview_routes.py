"""Recruiter endpoints for interviewee selection."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends

from app.auth import require_recruiter
from app.models.user import User
from app.services.interviews.schemas import SaveShortlistRequest
from app.services.interviews.selections import load_shortlist, save_shortlist
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


@router.put("/jobs/{job_id}/shortlist")
async def update_shortlist(
    job_id: UUID,
    request: SaveShortlistRequest,
    recruiter: Recruiter,
):
    return await save_shortlist(job_id, recruiter.id, request)


@router.get("/jobs/{job_id}/shortlist")
async def get_shortlist(
    job_id: UUID,
    recruiter: Recruiter,
):
    return await load_shortlist(job_id, recruiter.id)
