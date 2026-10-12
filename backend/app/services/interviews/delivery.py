"""Shared invitation review/confirmation and queue operations for UI and chat."""
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

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
    InvitationBatch, InvitationEmailJob,
)
from app.models.jd import JobDescription, JDConversation
from app.services.cv.screening import get_screening_results, lock_screening_job, owned_job
from app.services.interviews.invitations import create_invitation, INSTRUCTIONS, token_hash

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


async def preview_data(db, job_id, recruiter_id, mode):
    # Must run before reading ORM rows: screening reloads the session's state.
    results = await get_screening_results(job_id, recruiter_id, db=db)
    job = await owned_job(db, job_id, recruiter_id)
    settings = get_settings()
    subject = ("Interview invitation — " + " ".join(job.title.split()))[:250]
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
    current = {item["application_id"]: item for item in results["ranked"]}
    recipients = []
    now = datetime.now(timezone.utc)
    for member, candidate in rows:
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
        active = invitation is not None and invitation.revoked_at is None and invitation.expires_at > now
        action, reason = "skip", "Already invited; use retry or explicit reissue."
        if mode == "new" and invitation is None:
            action, reason = "create", "New invitation"
        elif mode == "retry_failed" and send_job and send_job.status == "failed" and active and invitation.recipient_email == email:
            try:
                stored = decrypt_message(send_job.encrypted_message)
                private_token = stored["body"].rsplit("#", 1)[1]
                base = str(TypeAdapter(AnyHttpUrl).validate_python(settings.public_app_url)).rstrip('/')
                expected_body = INSTRUCTIONS + f"\n\n{base}/interview#{private_token}"
                sender = str(TypeAdapter(EmailStr).validate_python(settings.smtp_username))
                if (stored["subject"] != subject or stored["sender"] != sender or stored["recipient"] != email
                    or stored["body"] != expected_body or token_hash(private_token) != invitation.token_hash):
                    raise ValueError("Stored email differs from the new preview")
            except Exception:
                raise HTTPException(409, "The original email no longer matches. Use explicit reissue.") from None
            action, reason = "retry", "Retry a definite failure using the existing link"
        elif mode == "reissue":
            if send_job and send_job.status == "sending":
                raise HTTPException(409, "An email is being sent. Wait before replacing its link.")
            action, reason = "replace" if invitation else "create", "Issue a new link; any previous link stops working"
        recipients.append({"application_id": str(member.application_id), "name": candidate.full_name,
            "email": email, "assessment_id": str(member.assessment_id), "score": member.score_snapshot,
            "rank": member.rank_snapshot, "action": action, "reason": reason,
            "invitation_id": str(invitation.id) if invitation else None,
            "token_hash": invitation.token_hash if invitation else None,
            "status": send_job.status if send_job else None})
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
        "body": INSTRUCTIONS + "\n\n{interview_link}", "public_app_url": str(base_url),
        "link_days": settings.interview_link_days, "recipients": recipients}


def fingerprint(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def public_preview(batch_id, key, data, expires_at):
    # Neither raw link tokens nor verification hashes enter chat or UI previews.
    return {"batch_id": str(batch_id), "preview_key": key, "expires_at": expires_at.isoformat(),
        "job_id": data["job_id"], "job_number": data["job_number"], "job_title": data["job_title"],
        "mode": data["mode"], "sender": data["sender"], "subject": data["subject"], "body": data["body"],
        "link_days": data["link_days"], "send_count": sum(r["action"] != "skip" for r in data["recipients"]),
        "recipients": [{k: r[k] for k in ("application_id", "name", "email", "action", "reason")} for r in data["recipients"]]}


async def prepare_invitations(job_id, recruiter_id, mode: SendMode = "new"):
    if mode not in {"new", "retry_failed", "reissue"}:
        raise HTTPException(422, "Invalid invitation action.")
    async with SessionFactory() as db:
        await lock_screening_job(db, job_id)
        data = await preview_data(db, job_id, recruiter_id, mode)
        batch = InvitationBatch(job_description_id=job_id, recruiter_id=recruiter_id,
            mode=mode, preview_key=fingerprint(data), preview=data,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10))
        db.add(batch)
        await db.flush()
        response = public_preview(batch.id, batch.preview_key, data, batch.expires_at)
        await db.commit()
        return response


async def confirm_invitations(job_id, recruiter_id, batch_id, preview_key):
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
        # This reload expires ORM instances, so keep only the scalar values above.
        data = await preview_data(db, job_id, recruiter_id, mode)
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
                send_job = await db.scalar(select(InvitationEmailJob).where(InvitationEmailJob.invitation_id == invitation.id))
                if send_job is None:
                    send_job = InvitationEmailJob(invitation_id=invitation.id, batch_id=batch_id)
                    db.add(send_job)
                send_job.encrypted_message = encrypt_message({"recipient": recipient["email"],
                    "subject": data["subject"], "body": data["body"].replace("{interview_link}", link),
                    "sender": data["sender"]})
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
        return [{"id": str(inv.id), "application_id": str(inv.application_id), "name": name,
            "email": inv.recipient_email, "status": send_job.status if send_job else "not_queued",
            "attempts": send_job.attempts if send_job else 0, "error_code": send_job.error_code if send_job else None,
            "link_status": "revoked" if inv.revoked_at else "expired" if inv.expires_at <= now else "active",
            "expires_at": inv.expires_at.isoformat()} for inv, send_job, name in rows]
