"""Recruiter endpoints for interviewee selection."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, SecretStr, Field
from app.services.interviews.delivery import (
    prepare_invitations, confirm_invitations, invitation_status, SendMode,
    invitation_options,
)

from app.auth import require_recruiter
from app.models.user import User
from app.services.interviews.schemas import SaveShortlistRequest
from app.services.interviews.selections import load_shortlist, save_shortlist
from app.services.interviews.invitations import validate_entry, revoke_invitation
from app.services.interviews.scheduling import InvitationSchedule
from app.services.interviews.sessions import (
    start_interview, recruiter_status, admit_candidate,
    candidate_status, candidate_join, recruiter_join, end_interview,
)
from app.services.interviews.shortlisting import (
    ShortlistRequest,
    preview_shortlist,
)
from app.services.interviews.transcripts import get_transcript

router = APIRouter(prefix="/interviews", tags=["Interviews"])
Recruiter = Annotated[User, Depends(require_recruiter)]


class InterviewEntryRequest(BaseModel):
    token: SecretStr


@router.post("/entry")
async def interview_entry(request: InterviewEntryRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await validate_entry(request.token.get_secret_value())


@router.post("/jobs/{job_id}/invitations/{invitation_id}/revoke")
async def revoke_interview_link(job_id: UUID, invitation_id: UUID, recruiter: Recruiter):
    return await revoke_invitation(job_id, recruiter.id, invitation_id)


class InvitationPreviewRequest(BaseModel):
    mode: SendMode = "new"
    schedules: list[InvitationSchedule] = Field(default_factory=list, max_length=200)
    include_calendar: bool = True


class InvitationConfirmation(BaseModel):
    batch_id: UUID
    preview_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    confirmed: Literal[True]


@router.post("/jobs/{job_id}/invitations/preview")
async def invitation_preview(job_id: UUID, request: InvitationPreviewRequest, recruiter: Recruiter):
    return await prepare_invitations(job_id, recruiter.id, request.mode,
        schedules=request.schedules, include_calendar=request.include_calendar)


@router.get("/jobs/{job_id}/invitations/options")
async def invitation_defaults(job_id: UUID, recruiter: Recruiter):
    return await invitation_options(job_id, recruiter.id)


@router.post("/jobs/{job_id}/invitations/confirm")
async def invitation_confirm(job_id: UUID, request: InvitationConfirmation, recruiter: Recruiter):
    return await confirm_invitations(job_id, recruiter.id, request.batch_id, request.preview_key)


@router.get("/jobs/{job_id}/invitations")
async def list_invitations(job_id: UUID, recruiter: Recruiter):
    return await invitation_status(job_id, recruiter.id)


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


@router.post("/jobs/{job_id}/applications/{application_id}/session/start")
async def start_session(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await start_interview(job_id, application_id, recruiter.id)


@router.get("/jobs/{job_id}/applications/{application_id}/transcript")
async def read_transcript(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await get_transcript(job_id, application_id, recruiter.id)


class FinalizeTranscriptRequest(BaseModel):
    transcript_id: UUID


@router.post("/jobs/{job_id}/applications/{application_id}/transcript/finalize")
async def finalize_saved_transcript(job_id: UUID, application_id: UUID,
                                    request: FinalizeTranscriptRequest, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    from app.services.interviews.transcript_finalization import retry_finalization
    return await retry_finalization(job_id, application_id, recruiter.id, request.transcript_id)


@router.get("/jobs/{job_id}/applications/{application_id}/session")
async def get_session(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await recruiter_status(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/session/admit")
async def admit_session(job_id: UUID, application_id: UUID, recruiter: Recruiter):
    return await admit_candidate(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/session/join")
async def join_host(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await recruiter_join(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/session/end")
async def end_session(job_id: UUID, application_id: UUID, recruiter: Recruiter):
    return await end_interview(job_id, application_id, recruiter.id)


@router.post("/candidate/status")
async def get_candidate_status(request: InterviewEntryRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await candidate_status(request.token.get_secret_value())


@router.post("/candidate/join")
async def join_candidate(request: InterviewEntryRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await candidate_join(request.token.get_secret_value())
