"""Recruiter-owned sessions, candidate admission and LiveKit access."""

from datetime import datetime, timezone
from uuid import uuid4

from aiohttp import ClientError
from fastapi import HTTPException
from livekit import api
from sqlalchemy import select

from app.db.session import SessionFactory
from app.models.interviews import (
    InterviewInvitation, InterviewSession,
    InterviewShortlist, InterviewShortlistMember,
)
from app.services.cv.screening import lock_screening_job, owned_application
from app.services.interviews.invitations import valid_invitation
from app.services.interviews.livekit import create_join_token, close_room
from app.services.interviews.identification import identity_row, admission_ready, is_admitted


def session_details(session):
    return {
        "id": str(session.id),
        "application_id": str(session.application_id),
        "status": session.status,
        "started_at": session.started_at,
        "completed_at": session.completed_at,
    }


async def owned_session(db, job_id, application_id, recruiter_id):
    await lock_screening_job(db, job_id)
    await owned_application(db, job_id, application_id, recruiter_id)
    return await db.scalar(
        select(InterviewSession).where(
            InterviewSession.application_id == application_id,
            InterviewSession.job_description_id == job_id,
            InterviewSession.recruiter_id == recruiter_id,
        ).execution_options(populate_existing=True)
    )


async def active_invitation(db, job_id, application_id, recruiter_id):
    invitation = await db.scalar(select(InterviewInvitation).where(
        InterviewInvitation.application_id == application_id,
        InterviewInvitation.job_description_id == job_id,
        InterviewInvitation.recruiter_id == recruiter_id,
        InterviewInvitation.revoked_at.is_(None),
        InterviewInvitation.expires_at > datetime.now(timezone.utc),
    ))
    if invitation is None:
        raise HTTPException(409, "An active candidate invitation is required.")
    return invitation


def require_active(session):
    if session is None:
        raise HTTPException(404, "Interview session not found.")
    if session.status != "in_progress":
        raise HTTPException(409, "This interview is not active.")


async def start_interview(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        member_id = await db.scalar(
            select(InterviewShortlistMember.id)
            .join(InterviewShortlist)
            .where(
                InterviewShortlistMember.application_id == application_id,
                InterviewShortlist.job_description_id == job_id,
                InterviewShortlist.recruiter_id == recruiter_id,
            )
        )
        if member_id is None:
            raise HTTPException(409, "Candidate must be in this job's saved selection.")
        await active_invitation(db, job_id, application_id, recruiter_id)

        if session is None:
            session = InterviewSession(
                id=uuid4(), application_id=application_id,
                job_description_id=job_id, recruiter_id=recruiter_id,
                room_name=f"interview-{uuid4()}", status="ready", version=1,
            )
            db.add(session)
        if session.status not in {"ready", "in_progress"}:
            raise HTTPException(409, "This interview cannot be started.")

        token = create_join_token(session.room_name, f"recruiter-{recruiter_id}")
        if session.status == "ready":
            session.status = "in_progress"
            session.version += 1
        await db.flush()
        details = session_details(session)
        await db.commit()
        return {**details, **token}


async def recruiter_status(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        if session is None:
            return {"status": "not_started", "candidate_admitted": False}
        invitation = await db.scalar(select(InterviewInvitation).where(
            InterviewInvitation.application_id == application_id,
            InterviewInvitation.job_description_id == job_id,
            InterviewInvitation.recruiter_id == recruiter_id,
        ))
        identity = await identity_row(db, application_id)
        admitted = bool(
            invitation
            and invitation.revoked_at is None
            and invitation.expires_at > datetime.now(timezone.utc)
            and is_admitted(session, invitation, identity)
        )
        return {**session_details(session), "candidate_admitted": admitted}


async def admit_candidate(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        require_active(session)
        invitation = await active_invitation(db, job_id, application_id, recruiter_id)
        identity = await identity_row(db, application_id)
        if not admission_ready(identity):
            raise HTTPException(409, "Wait for photo verification or record a manual review before admitting the candidate.")
        if not is_admitted(session, invitation, identity):
            session.candidate_admitted_at = datetime.now(timezone.utc)
            if session.started_at is None:
                session.started_at = session.candidate_admitted_at
            session.admitted_invitation_hash = invitation.token_hash
            session.version += 1
        details = session_details(session)
        await db.commit()
    from app.services.interviews.live_transcription import start_transcription
    start_transcription(session.id)
    return {**details, "candidate_admitted": True}


async def candidate_session(db, token):
    invitation = await valid_invitation(db, token)
    session = await owned_session(
        db, invitation.job_description_id,
        invitation.application_id, invitation.recruiter_id,
    )
    # Recheck after acquiring the job lock: expiry/reissue/revoke may have changed.
    invitation = await valid_invitation(db, token)
    return invitation, session


async def candidate_status(token):
    async with SessionFactory() as db:
        invitation, session = await candidate_session(db, token)
        if session is None or session.status == "ready":
            return {"status": "waiting_for_recruiter", "can_join": False}
        identity = await identity_row(db, invitation.application_id)
        admitted = is_admitted(session, invitation, identity)
        return {
            "status": session.status,
            "can_join": admitted,
            "waiting_for_admission": session.status == "in_progress" and not admitted,
        }


async def candidate_join(token):
    async with SessionFactory() as db:
        invitation, session = await candidate_session(db, token)
        require_active(session)
        identity = await identity_row(db, invitation.application_id)
        if not is_admitted(session, invitation, identity):
            raise HTTPException(403, "Wait for the recruiter to admit you.")
        credentials = create_join_token(session.room_name, f"candidate-{invitation.application_id}")
        session_id = session.id
    from app.services.interviews.live_transcription import start_transcription
    start_transcription(session_id)
    return credentials


async def recruiter_join(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        require_active(session)
        credentials = create_join_token(session.room_name, f"recruiter-{recruiter_id}")
        session_id, admitted = session.id, session.candidate_admitted_at is not None
    if admitted:
        from app.services.interviews.live_transcription import start_transcription
        start_transcription(session_id)
    return credentials


async def end_interview(job_id, application_id, recruiter_id):
    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        if session is None:
            raise HTTPException(404, "Interview session not found.")
        if session.status == "completed":
            return session_details(session)
        if session.status not in {"ready", "in_progress", "closing"}:
            raise HTTPException(409, "This interview cannot be ended.")
        if session.status != "closing":
            session.status = "closing"
            session.version += 1
        room_name = session.room_name
        await db.commit()

    try:
        # Flush speech results before deleting the room and its audio tracks.
        from app.services.interviews.live_transcription import stop_transcription
        await stop_transcription(session.id)
        await close_room(room_name)
    except (api.TwirpError, ClientError, TimeoutError):
        raise HTTPException(503, "Room closure is pending. Retry End interview.") from None

    async with SessionFactory() as db:
        session = await owned_session(db, job_id, application_id, recruiter_id)
        if session is None:
            raise HTTPException(404, "Interview session not found.")
        if session.status == "closing":
            session.status = "completed"
            session.completed_at = datetime.now(timezone.utc)
            session.version += 1
        details = session_details(session)
        await db.commit()
        return details
