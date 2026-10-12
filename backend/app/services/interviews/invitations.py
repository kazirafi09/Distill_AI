"""Create and validate individual interview links; sending is a separate step."""
import re
import secrets
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from fastapi import HTTPException
from pydantic import AnyHttpUrl, EmailStr, TypeAdapter
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.cv import Candidate, CandidateApplication
from app.models.interviews import InterviewInvitation, InterviewShortlist, InterviewShortlistMember
from app.models.jd import JobDescription
from app.services.cv.screening import lock_screening_job, owned_job

INSTRUCTIONS = "You have been selected. Use this link to join the interview."


def token_hash(token):
    return sha256(token.encode("ascii")).hexdigest()


def new_link():
    settings = get_settings()
    base = TypeAdapter(AnyHttpUrl).validate_python(settings.public_app_url)
    if base.username or base.password or base.query or base.fragment:
        raise ValueError("Public application URL must not contain credentials, query or fragment.")
    token = secrets.token_urlsafe(32)
    # Fragments are not sent in browser HTTP URLs or server access logs.
    link = f"{str(base).rstrip('/')}/interview#{token}"
    expires_at = datetime.now(timezone.utc) + timedelta(days=settings.interview_link_days)
    return token, link, expires_at


async def create_invitation(db, job_id, recruiter_id, application_id, recipient_email, *, replace=False):
    """Called by the future confirmed-send service, inside its transaction.

    Return the record and raw link only for the private email job payload.
    Do not return the link in general recruiter/chat responses or log it.
    """
    await lock_screening_job(db, job_id)
    await owned_job(db, job_id, recruiter_id)
    candidate = await db.scalar(
        select(Candidate).join(CandidateApplication)
        .join(InterviewShortlistMember, InterviewShortlistMember.application_id == CandidateApplication.id)
        .join(InterviewShortlist, InterviewShortlist.id == InterviewShortlistMember.shortlist_id)
        .where(CandidateApplication.id == application_id,
               CandidateApplication.job_description_id == job_id,
               Candidate.recruiter_id == recruiter_id,
               InterviewShortlist.job_description_id == job_id,
               InterviewShortlist.recruiter_id == recruiter_id)
    )
    if candidate is None:
        raise HTTPException(422, "Application must be in this job's saved shortlist.")
    email = str(TypeAdapter(EmailStr).validate_python(candidate.email))
    if email != str(TypeAdapter(EmailStr).validate_python(recipient_email)):
        raise HTTPException(409, "Recipient email changed. Review recipients again.")
    invitation = await db.scalar(select(InterviewInvitation).where(
        InterviewInvitation.application_id == application_id))
    if invitation is not None and not replace:
        raise HTTPException(409, "An invitation already exists. Use explicit reissue.")
    token, link, expires_at = new_link()
    if invitation is None:
        invitation = InterviewInvitation(recruiter_id=recruiter_id,
            job_description_id=job_id, application_id=application_id)
        db.add(invitation)
    invitation.recipient_email = email
    invitation.token_hash = token_hash(token)
    invitation.expires_at = expires_at
    invitation.revoked_at = None
    await db.flush()
    return invitation, link


async def validate_entry(token):
    if not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
        raise HTTPException(410, "This interview link is invalid or no longer active.")
    async with SessionFactory() as db:
        row = (await db.execute(
            select(InterviewInvitation, JobDescription.title)
            .join(JobDescription, JobDescription.id == InterviewInvitation.job_description_id)
            .where(InterviewInvitation.token_hash == token_hash(token))
        )).first()
        now = datetime.now(timezone.utc)
        if row is None or row[0].revoked_at is not None or row[0].expires_at <= now:
            raise HTTPException(410, "This interview link is invalid or no longer active.")
        invitation, title = row
        return {"job_title": title, "instructions": INSTRUCTIONS,
                "expires_at": invitation.expires_at.isoformat(), "interview_ready": False}


async def revoke_invitation(job_id, recruiter_id, invitation_id):
    async with SessionFactory() as db:
        await lock_screening_job(db, job_id)
        await owned_job(db, job_id, recruiter_id)
        invitation = await db.scalar(select(InterviewInvitation).where(
            InterviewInvitation.id == invitation_id,
            InterviewInvitation.job_description_id == job_id,
            InterviewInvitation.recruiter_id == recruiter_id))
        if invitation is None:
            raise HTTPException(404, "Invitation not found.")
        if invitation.revoked_at is None:
            invitation.revoked_at = datetime.now(timezone.utc)
        await db.commit()
        return {"id": str(invitation_id), "status": "revoked"}
