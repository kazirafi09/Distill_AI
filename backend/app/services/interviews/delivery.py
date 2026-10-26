"""Shared invitation review/confirmation and queue operations for UI and chat."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
from pydantic import AnyHttpUrl, EmailStr, TypeAdapter, ValidationError
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.integrations.email_mcp import signing_secret
from app.models.cv import Candidate, CandidateApplication
from app.models.interviews import (
    InterviewInvitation, InterviewShortlist, InterviewShortlistMember,
    InvitationBatch, InvitationEmailJob, InterviewSession, InvitationScheduleDraft,
)
from app.models.jd import JobDescription, JDConversation
from app.services.cv.screening import get_screening_results, lock_screening_job, owned_job, available_processing_versions
from app.services.interviews.invitations import create_invitation, INSTRUCTIONS, token_hash
from app.services.interviews.invitation_content import invitation_subject, invitation_body, private_interview_link
from app.services.interviews.scheduling import InvitationSchedule, application_session, schedule_for_session, JOIN_EARLY_MINUTES
from app.integrations.calendar_event import CalendarEvent

SendMode = Literal["new", "retry_failed", "reissue"]


def message_cipher():
    # The stable app secret protects link-bearing queue payloads at rest.
    secret = get_settings().jwt_secret_key.get_secret_value()
    key = hashlib.sha256(("invitation-email-payload:" + secret).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def encrypt_message(payload):
    return message_cipher().encrypt(json.dumps(payload).encode()).decode()


def decrypt_message(payload):
    return json.loads(message_cipher().decrypt(payload.encode()))


async def resolve_job(recruiter_id, job_number=None, job_id=None):
    async with SessionFactory() as db:
        query = select(JobDescription.id).join(JDConversation).where(JDConversation.recruiter_id == recruiter_id)
        if job_number is not None:
            query = query.where(JobDescription.number == job_number)
        elif job_id is not None:
            query = query.where(JobDescription.id == job_id)
        else:
            raise HTTPException(422, "Specify a job number or open a saved JD first.")
        identifier = await db.scalar(query)
        if identifier is None:
            raise HTTPException(404, "Job not found.")
        return identifier


async def invitation_options(job_id, recruiter_id):
    async with SessionFactory() as db:
        await owned_job(db, job_id, recruiter_id)
        rows = (await db.execute(select(CandidateApplication.id, Candidate.full_name, Candidate.email)
            .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
            .join(InterviewShortlistMember, InterviewShortlistMember.application_id == CandidateApplication.id)
            .join(InterviewShortlist, InterviewShortlist.id == InterviewShortlistMember.shortlist_id)
            .where(CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id,
                   InterviewShortlist.job_description_id == job_id, InterviewShortlist.recruiter_id == recruiter_id)
            .order_by(InterviewShortlistMember.rank_snapshot, CandidateApplication.id))).all()
        recipients = []
        for application_id, name, email in rows:
            session = await application_session(db, application_id)
            draft = await db.get(InvitationScheduleDraft, application_id)
            recipients.append({"application_id": str(application_id), "name": name, "email": email,
                               "schedule": schedule_for_session(draft or session)})
        return {"join_early_minutes": JOIN_EARLY_MINUTES, "recipients": recipients}


async def save_invitation_schedules(job_id, recruiter_id, schedules):
    """Save all selected recipients' plans atomically, without issuing links or mail."""
    checked = [item if isinstance(item, InvitationSchedule) else InvitationSchedule.model_validate(item)
               for item in schedules]
    requested = {item.application_id: item for item in checked}
    if len(requested) != len(checked):
        raise HTTPException(422, "Provide each candidate's schedule only once.")
    if any(item.ends_at <= datetime.now(timezone.utc) for item in checked):
        raise HTTPException(422, "Choose an end time in the future.")
    async with SessionFactory() as db:
        await lock_screening_job(db, job_id)
        await owned_job(db, job_id, recruiter_id)
        selected = set(await db.scalars(select(CandidateApplication.id)
            .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
            .join(InterviewShortlistMember, InterviewShortlistMember.application_id == CandidateApplication.id)
            .join(InterviewShortlist, InterviewShortlist.id == InterviewShortlistMember.shortlist_id)
            .where(CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id,
                   InterviewShortlist.job_description_id == job_id, InterviewShortlist.recruiter_id == recruiter_id)))
        if not selected or set(requested) != selected:
            raise HTTPException(409, "The saved interviewees changed. Reload and set times for every recipient.")
        for application_id, item in requested.items():
            session = await application_session(db, application_id)
            if session and session.status not in {"ready", "scheduled"}:
                raise HTTPException(409, "An active or completed interview cannot be rescheduled through an invitation.")
            draft = await db.get(InvitationScheduleDraft, application_id)
            if draft is None:
                draft = InvitationScheduleDraft(application_id=application_id)
                db.add(draft)
            draft.starts_at, draft.ends_at = item.starts_at, item.ends_at
            draft.display_timezone = item.display_timezone
        await db.commit()
    return {"saved": len(requested), "message": "Interview times saved. You can edit them or ask the assistant to prepare invitations."}


async def preview_data(db, job_id, recruiter_id, mode, schedules=None, include_calendar=True, *, versions=None):
    # Calendar attachments are automatic for new/reissued mail. Retries retain
    # the exact previously confirmed payload, including legacy calendar settings.
    if mode != "retry_failed":
        include_calendar = True
    # Must run before reading ORM rows: screening reloads the session's state.
    results = await get_screening_results(job_id, recruiter_id, db=db, versions=versions)
    job = await owned_job(db, job_id, recruiter_id)
    settings = get_settings()
    subject = invitation_subject(job.title)
    requested = {}
    for item in schedules or []:
        checked = item if isinstance(item, InvitationSchedule) else InvitationSchedule.model_validate(item)
        identifier = str(checked.application_id)
        if identifier in requested:
            raise HTTPException(422, "Provide each candidate's schedule only once.")
        requested[identifier] = checked.public()
    if not results["run"] or results["run"]["status"] == "outdated" or results["warnings"]:
        raise HTTPException(409, "Current screening results are required. Refresh the shortlist.")
    shortlist = await db.scalar(select(InterviewShortlist).where(
        InterviewShortlist.job_description_id == job_id, InterviewShortlist.recruiter_id == recruiter_id))
    if shortlist is None:
        raise HTTPException(422, "Save the interviewees before preparing invitations.")
    rows = (await db.execute(select(InterviewShortlistMember, Candidate)
        .join(CandidateApplication, CandidateApplication.id == InterviewShortlistMember.application_id)
        .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
        .where(InterviewShortlistMember.shortlist_id == shortlist.id,
               CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id)
        .order_by(InterviewShortlistMember.application_id))).all()
    if not rows:
        raise HTTPException(422, "The saved interviewee list is empty.")
    if set(requested) - {str(member.application_id) for member, _ in rows}:
        raise HTTPException(422, "Schedules must belong to the saved recipients.")
    current = {item["application_id"]: item for item in results["ranked"]}
    recipients = []
    now = datetime.now(timezone.utc)
    for member, candidate in rows:
        calendar_enabled = include_calendar
        assessment = current.get(str(member.application_id))
        if (str(shortlist.screening_run_id) != results["run"]["id"] or not assessment
            or assessment["id"] != str(member.assessment_id)
            or assessment["score"] != member.score_snapshot or assessment["rank"] != member.rank_snapshot):
            raise HTTPException(409, "Saved interviewees are outdated. Review and save them again.")
        try:
            email = str(TypeAdapter(EmailStr).validate_python(candidate.email))
        except ValidationError:
            raise HTTPException(422, "A selected candidate has an invalid email address.") from None
        invitation = await db.scalar(select(InterviewInvitation).where(
            InterviewInvitation.application_id == member.application_id))
        send_job = await db.scalar(select(InvitationEmailJob).where(
            InvitationEmailJob.invitation_id == invitation.id)) if invitation else None
        session = await application_session(db, member.application_id)
        current_schedule = schedule_for_session(session)
        draft = await db.get(InvitationScheduleDraft, member.application_id)
        planned_schedule = schedule_for_session(draft)
        schedule = (current_schedule if mode == "retry_failed" else
                    requested.get(str(member.application_id), planned_schedule or current_schedule))
        active = invitation is not None and invitation.revoked_at is None and invitation.expires_at > now
        action, reason = "skip", "Already invited; use retry or explicit reissue."
        if mode == "new" and invitation is None:
            action, reason = "create", "New invitation"
        elif mode == "retry_failed" and send_job and send_job.status == "failed" and active and invitation.recipient_email == email:
            try:
                stored = decrypt_message(send_job.encrypted_message)
                link = private_interview_link(stored)
                private_token = link.rsplit("#", 1)[1]
                base = str(TypeAdapter(AnyHttpUrl).validate_python(settings.public_app_url)).rstrip('/')
                if stored.get("schedule") is not None:
                    if schedule != stored["schedule"]:
                        raise ValueError("Schedule changed")
                    calendar_enabled = stored.get("include_calendar", False)
                    expected_body = invitation_body(job.title, candidate.full_name, schedule, link,
                        include_calendar=calendar_enabled)
                    expected_subject = subject
                else:
                    calendar_enabled = False
                    expected_body = INSTRUCTIONS + f"\n\n{base}/interview#{private_token}"
                    expected_subject = ("Interview invitation — " + " ".join(job.title.split()))[:250]
                sender = str(TypeAdapter(EmailStr).validate_python(settings.smtp_username))
                if (stored["subject"] != expected_subject or stored["sender"] != sender or stored["recipient"] != email
                    or stored["body"] != expected_body or token_hash(private_token) != invitation.token_hash):
                    raise ValueError("Stored email differs from the new preview")
            except Exception:
                raise HTTPException(409, "The original email no longer matches. Use explicit reissue.") from None
            action, reason = "retry", "Retry a definite failure using the existing link"
        elif mode == "reissue":
            if send_job and send_job.status == "sending":
                raise HTTPException(409, "An email is being sent. Wait before replacing its link.")
            action, reason = "replace" if invitation else "create", "Issue a new link; any previous link stops working"
        if action in {"create", "replace"}:
            if schedule is None:
                raise HTTPException(422, "Choose a start and end time for each candidate before reviewing invitations.")
            if datetime.fromisoformat(schedule["ends_at"]) <= now:
                raise HTTPException(422, "This interview window has ended. Choose an end time in the future.")
            if session and session.status not in {"ready", "scheduled"}:
                raise HTTPException(409, "An active or completed interview cannot be rescheduled through an invitation.")
        recipients.append({"application_id": str(member.application_id), "name": candidate.full_name,
            "email": email, "assessment_id": str(member.assessment_id), "score": member.score_snapshot,
            "rank": member.rank_snapshot, "action": action, "reason": reason,
            "invitation_id": str(invitation.id) if invitation else None,
            "token_hash": invitation.token_hash if invitation else None,
            "status": send_job.status if send_job else None, "schedule": schedule,
            "subject": stored["subject"] if action == "retry" else subject,
            "include_calendar": calendar_enabled,
            "session_version": session.version if session else None, "saved_schedule": current_schedule,
            "planned_schedule": planned_schedule if mode != "retry_failed" else None,
            "body": stored["body"].replace(link, "{interview_link}") if action == "retry" and not stored.get("schedule")
                else invitation_body(job.title, candidate.full_name, schedule, include_calendar=calendar_enabled)})
    try:
        sender = str(TypeAdapter(EmailStr).validate_python(settings.smtp_username))
        base_url = TypeAdapter(AnyHttpUrl).validate_python(settings.public_app_url)
        if base_url.username or base_url.password or base_url.query or base_url.fragment:
            raise ValueError("Invalid public URL")
        signing_secret()
        if not settings.smtp_password or not settings.smtp_password.get_secret_value():
            raise ValueError("SMTP password missing")
    except (ValueError, TypeError):
        raise HTTPException(503, "Configure SMTP, email MCP and the public application URL first.") from None
    return {"job_id": str(job_id), "job_number": job.number, "job_title": job.title,
        "mode": mode, "sender": sender, "subject": subject,
        "body": invitation_body(job.title, include_calendar=include_calendar), "public_app_url": str(base_url),
        "include_calendar": any(r["include_calendar"] for r in recipients if r["action"] != "skip") if mode == "retry_failed" else include_calendar,
        "link_days": settings.interview_link_days, "recipients": recipients}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_preview(batch_id, key, data, expires_at):
    # Neither raw link tokens nor verification hashes enter chat or UI previews.
    return {"batch_id": str(batch_id), "preview_key": key, "expires_at": expires_at.isoformat(),
        "job_id": data["job_id"], "job_number": data["job_number"], "job_title": data["job_title"],
        "mode": data["mode"], "sender": data["sender"], "subject": data["subject"], "body": data["body"],
        "link_days": data["link_days"], "send_count": sum(r["action"] != "skip" for r in data["recipients"]),
        "include_calendar": data.get("include_calendar", False),
        "recipients": [{k: r[k] for k in ("application_id", "name", "email", "action", "reason", "schedule", "subject", "body", "include_calendar")} for r in data["recipients"]]}


async def prepare_invitations(job_id, recruiter_id, mode: SendMode = "new", *, schedules=None, include_calendar=True):
    if mode not in {"new", "retry_failed", "reissue"}:
        raise HTTPException(422, "Invalid invitation action.")
    versions = await available_processing_versions()
    async with SessionFactory() as db:
        await lock_screening_job(db, job_id)
        data = await preview_data(db, job_id, recruiter_id, mode, schedules, include_calendar, versions=versions)
        batch = InvitationBatch(job_description_id=job_id, recruiter_id=recruiter_id,
            mode=mode, preview_key=fingerprint(data), preview=data,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        db.add(batch)
        await db.flush()
        response = public_preview(batch.id, batch.preview_key, data, batch.expires_at)
        await db.commit()
        return response


async def confirm_invitations(job_id, recruiter_id, batch_id, preview_key):
    versions = await available_processing_versions()
    async with SessionFactory() as db:
        await lock_screening_job(db, job_id)
        await owned_job(db, job_id, recruiter_id)
        batch = await db.scalar(select(InvitationBatch).where(InvitationBatch.id == batch_id,
            InvitationBatch.recruiter_id == recruiter_id, InvitationBatch.job_description_id == job_id))
        if batch is None or batch.preview_key != preview_key:
            raise HTTPException(409, "Review the exact invitation preview first.")
        if batch.confirmed_at is not None:
            return {"batch_id": str(batch_id), "already_confirmed": True, "message": "No additional emails queued."}
        if batch.expires_at <= datetime.now(timezone.utc):
            raise HTTPException(409, "Invitation preview expired. Prepare it again.")
        mode = batch.mode
        schedules = [{"application_id": r["application_id"], **r["schedule"]}
                     for r in batch.preview["recipients"] if r.get("schedule")]
        include_calendar = batch.preview.get("include_calendar", False)
        # This reload expires ORM instances, so keep only the scalar values above.
        data = await preview_data(db, job_id, recruiter_id, mode, schedules, include_calendar, versions=versions)
        if fingerprint(data) != preview_key:
            raise HTTPException(409, "Recipients, selection, message or invitation state changed. Review again.")
        batch = await db.get(InvitationBatch, batch_id)
        queued = 0
        for recipient in data["recipients"]:
            if recipient["action"] == "skip":
                continue
            if recipient["action"] == "retry":
                send_job = await db.scalar(select(InvitationEmailJob).where(
                    InvitationEmailJob.invitation_id == UUID(recipient["invitation_id"])))
                try:
                    decrypt_message(send_job.encrypted_message)
                except Exception:
                    raise HTTPException(409, "Stored email cannot be decrypted. Use explicit reissue.") from None
            else:
                invitation, link = await create_invitation(db, job_id, recruiter_id,
                    UUID(recipient["application_id"]), recipient["email"], replace=recipient["action"] == "replace")
                schedule = recipient["schedule"]
                session = await application_session(db, UUID(recipient["application_id"]))
                if session is None:
                    session = InterviewSession(id=uuid4(), application_id=UUID(recipient["application_id"]),
                        job_description_id=job_id, recruiter_id=recruiter_id, room_name=f"interview-{uuid4()}", status="ready", version=1)
                    db.add(session)
                else:
                    session.version += 1
                session.starts_at = datetime.fromisoformat(schedule["starts_at"])
                session.ends_at = datetime.fromisoformat(schedule["ends_at"])
                session.display_timezone = schedule["display_timezone"]
                draft = await db.get(InvitationScheduleDraft, UUID(recipient["application_id"]))
                if draft is not None:
                    await db.delete(draft)
                invitation.expires_at = session.ends_at
                send_job = await db.scalar(select(InvitationEmailJob).where(InvitationEmailJob.invitation_id == invitation.id))
                if send_job is None:
                    send_job = InvitationEmailJob(invitation_id=invitation.id, batch_id=batch_id)
                    db.add(send_job)
                calendar = CalendarEvent(uid=invitation.id, sequence=session.version, title=data["subject"],
                    starts_at=session.starts_at, ends_at=session.ends_at, link=link) if include_calendar else None
                send_job.encrypted_message = encrypt_message({"recipient": recipient["email"],
                    "subject": data["subject"], "body": invitation_body(data["job_title"], recipient["name"], schedule, link, include_calendar=include_calendar),
                    "sender": data["sender"], "interview_link": link, "schedule": schedule,
                    "include_calendar": include_calendar, "calendar_event": calendar.model_dump(mode="json") if calendar else None})
                send_job.attempts = 0
            send_job.batch_id, send_job.status, send_job.error_code = batch_id, "queued", None
            send_job.provider_message_id = None
            queued += 1
        batch.confirmed_at = datetime.now(timezone.utc)
        await db.commit()
        return {"batch_id": str(batch_id), "queued": queued, "already_confirmed": False,
                "message": f"Queued {queued} individual emails. Queueing is not delivery."}


async def invitation_status(job_id, recruiter_id):
    async with SessionFactory() as db:
        await owned_job(db, job_id, recruiter_id)
        rows = (await db.execute(select(InterviewInvitation, InvitationEmailJob, Candidate.full_name)
            .join(CandidateApplication, CandidateApplication.id == InterviewInvitation.application_id)
            .join(Candidate, Candidate.id == CandidateApplication.candidate_id)
            .outerjoin(InvitationEmailJob, InvitationEmailJob.invitation_id == InterviewInvitation.id)
            .where(InterviewInvitation.job_description_id == job_id, InterviewInvitation.recruiter_id == recruiter_id)
            .order_by(InterviewInvitation.created_at))).all()
        now = datetime.now(timezone.utc)
        output = []
        for inv, send_job, name in rows:
            schedule = schedule_for_session(await application_session(db, inv.application_id))
            output.append({"id": str(inv.id), "application_id": str(inv.application_id), "name": name,
            "email": inv.recipient_email, "status": send_job.status if send_job else "not_queued",
            "attempts": send_job.attempts if send_job else 0, "error_code": send_job.error_code if send_job else None,
            "link_status": "revoked" if inv.revoked_at else "expired" if inv.expires_at <= now else "active",
            "expires_at": inv.expires_at.isoformat(), "schedule": schedule})
        return output
