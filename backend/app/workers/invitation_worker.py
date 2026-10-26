"""Durable invitation sender. Interrupted sends become uncertain, never retried blindly."""
import asyncio
import logging
from app.core.logging import setup_logging
from datetime import datetime, timezone
from uuid import uuid4
from pydantic import EmailStr, TypeAdapter

from sqlalchemy import select, text, update

from app.core.config import get_settings
from app.db.session import SessionFactory, engine
from app.integrations.email_mcp import EmailJob, send_email_job
from app.models.cv import Candidate, CandidateApplication
from app.models.interviews import InterviewInvitation, InvitationEmailJob
from app.models.user import User
from app.services.cv.screening import lock_screening_job
from app.services.interviews.delivery import decrypt_message
from app.services.interviews.invitations import token_hash
from app.services.interviews.invitation_content import private_interview_link
from app.services.interviews.scheduling import application_session, schedule_for_session

WORKER_LOCK = 73101984024


async def send_one():
    async with SessionFactory() as db:
        job_id = await db.scalar(select(InterviewInvitation.job_description_id)
            .join(InvitationEmailJob, InvitationEmailJob.invitation_id == InterviewInvitation.id)
            .where(InvitationEmailJob.status == "queued")
            .order_by(InvitationEmailJob.updated_at).limit(1))
        if job_id is None:
            return False
        await lock_screening_job(db, job_id)
        row = (await db.execute(select(InvitationEmailJob, InterviewInvitation, Candidate, User)
            .join(InterviewInvitation, InterviewInvitation.id == InvitationEmailJob.invitation_id)
            .join(CandidateApplication, CandidateApplication.id == InterviewInvitation.application_id)
            .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
            .join(User, User.id == InterviewInvitation.recruiter_id)
            .where(InvitationEmailJob.status == "queued", InterviewInvitation.job_description_id == job_id)
            .order_by(InvitationEmailJob.updated_at).limit(1))).first()
        if row is None:
            return True
        send_job, invitation, candidate, recruiter = row
        send_id = send_job.id
        try:
            payload = decrypt_message(send_job.encrypted_message)
            link = private_interview_link(payload)
            raw_token = link.rsplit("#", 1)[1]
            if (invitation.revoked_at or invitation.expires_at <= datetime.now(timezone.utc)
                or token_hash(raw_token) != invitation.token_hash
                or candidate.recruiter_id != recruiter.id
                or str(TypeAdapter(EmailStr).validate_python(candidate.email)) != invitation.recipient_email
                or not recruiter.is_active or recruiter.role != "recruiter"
                or payload["recipient"] != invitation.recipient_email
                or payload["sender"] != str(TypeAdapter(EmailStr).validate_python(get_settings().smtp_username))):
                raise ValueError("Invitation no longer matches the authorized email")
            session = None
            if payload.get("schedule") is not None:
                session = await application_session(db, invitation.application_id)
                if payload["schedule"] != schedule_for_session(session):
                    raise ValueError("Interview schedule changed after review")
            # Each attempt gets a distinct transport ID while reusing the durable job/link.
            message = EmailJob(job_id=uuid4(), recipient=payload["recipient"],
                subject=payload["subject"], body=payload["body"], calendar_event=payload.get("calendar_event"))
            if message.calendar_event and (session is None or str(message.calendar_event.link) != link
                or message.calendar_event.uid != invitation.id
                or message.calendar_event.starts_at != session.starts_at or message.calendar_event.ends_at != session.ends_at):
                raise ValueError("Calendar event differs from the reviewed interview")
        except Exception:
            send_job.status, send_job.error_code = "failed", "INVITATION_REVIEW_REQUIRED"
            await db.commit()
            return True
        send_job.status, send_job.error_code = "sending", None
        send_job.attempts += 1
        await db.commit()

    # SMTP/MCP runs outside the database lock. No automatic send retries.
    result = await send_email_job(message)
    async with SessionFactory() as db:
        await lock_screening_job(db, job_id)
        send_job = await db.get(InvitationEmailJob, send_id)
        if send_job is not None:
            send_job.status, send_job.error_code = result.status, result.error_code
            send_job.provider_message_id = result.provider_message_id
            await db.commit()
    return True


async def main():
    # Hold one process lock for the worker lifetime. A new owner recovers stale sends.
    async with engine.connect() as connection:
        acquired = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
        await connection.commit()
        if not acquired:
            return
        try:
            async with SessionFactory() as db:
                await db.execute(update(InvitationEmailJob).where(InvitationEmailJob.status == "sending")
                    .values(status="uncertain", error_code="WORKER_INTERRUPTED_DURING_SEND"))
                await db.commit()
            while True:
                # Unexpected failures restart the process; abandoned sending
                # records become uncertain when the next owner acquires the lock.
                did_work = await send_one()
                if not did_work:
                    await asyncio.sleep(3)
        finally:
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
            await connection.commit()


if __name__ == "__main__":
    setup_logging("invitation-worker")
    try:
        asyncio.run(main())
    except Exception as error:
        logging.getLogger("distill_ai.invitation_worker").error(
            "Invitation worker stopped | error_type=%s", type(error).__name__)
        raise
