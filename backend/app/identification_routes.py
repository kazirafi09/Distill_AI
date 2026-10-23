"""Private candidate and recruiter endpoints for F7."""
import base64
import binascii
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, ValidationError

from app.auth import require_recruiter
from app.models.user import User
from app.services.interviews import identification
from app.services.interviews.face_matching import MAX_IMAGE_BYTES

router = APIRouter(prefix="/interviews", tags=["Candidate identification"])
Recruiter = Annotated[User, Depends(require_recruiter)]


class CandidateRequest(BaseModel):
    token: SecretStr = Field(min_length=43, max_length=43)


class ConsentRequest(CandidateRequest):
    choice: Literal["accepted", "declined", "withdrawn"]
    version: str = Field(max_length=40)


class FrameRequest(CandidateRequest):
    frame: SecretStr = Field(max_length=7 * 1024 * 1024)


class ReviewRequest(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/candidate/identity")
async def candidate_identity(payload: CandidateRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await identification.candidate_identity(payload.token.get_secret_value())


@router.post("/candidate/identity/consent")
async def candidate_consent(payload: ConsentRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await identification.set_consent(payload.token.get_secret_value(), payload.choice, payload.version)


async def read_frame(request: Request):
    # Stream a bounded JSON body: multipart uploads may spool biometric frames to disk.
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 7 * 1024 * 1024:
            raise HTTPException(413, "Camera frame is too large.")
    try:
        payload = FrameRequest.model_validate_json(body)
        frame = base64.b64decode(payload.frame.get_secret_value(), validate=True)
    except (ValidationError, binascii.Error, ValueError):
        raise HTTPException(422, "Send a valid camera frame and invitation.") from None
    if not frame or len(frame) > MAX_IMAGE_BYTES:
        raise HTTPException(413, "Camera frame must be nonempty and at most 5 MB.")
    return payload.token.get_secret_value(), frame


@router.post("/candidate/identity/verify")
async def verify_candidate(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    token, frame = await read_frame(request)
    return await identification.verify_frame(token, frame)


@router.post("/candidate/identity/check-frame")
async def check_candidate_frame(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    token, frame = await read_frame(request)
    return await identification.check_frame(token, frame)


@router.get("/jobs/{job_id}/applications/{application_id}/identity")
async def recruiter_identity(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await identification.recruiter_identity(job_id, application_id, recruiter.id)


@router.post("/jobs/{job_id}/applications/{application_id}/identity/review")
async def review_identity(job_id: UUID, application_id: UUID, payload: ReviewRequest,
                          recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    note = payload.note.strip()
    if not note:
        raise HTTPException(422, "Describe your manual review.")
    return await identification.recruiter_identity(job_id, application_id, recruiter.id, review_note=note)


@router.post("/jobs/{job_id}/applications/{application_id}/identity/delete")
async def delete_identity(job_id: UUID, application_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await identification.recruiter_identity(job_id, application_id, recruiter.id, delete=True)
