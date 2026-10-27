"""Consent-gated verification with per-application locking and bounded retention."""
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException
from sqlalchemy import select, update
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.candidate_identity import CandidateIdentity
from app.models.cv import CandidateApplication
from app.models.interviews import InterviewSession
from app.services.interviews.invitations import valid_invitation
from app.services.interviews import face_matching
from app.services.recruitment_access import owned_application

CONSENT_VERSION = "face-verification-v3"
MAX_VERIFICATION_ATTEMPTS = 5
CONSENT_TEXT = (
    "I agree to compare one camera frame with the photo submitted in my application. "
    "After I agree, the consent window will close and my selected camera will appear in the waiting room. "
    "While the preview is open, camera frames are checked periodically to confirm only one face is visible. "
    "A five-second countdown starts when I am alone, resets if another face appears or my face is no longer visible, "
    "and restarts when only my face is visible again. One frame is then compared automatically. "
    "The images and face embeddings are processed in memory and are not saved by Distill. "
    "The recruiter sees the result and similarity score before deciding whether to admit me; "
    "this checks resemblance to my submitted photo, not official identity or liveness. "
    "I can decline and request manual review, or withdraw consent and delete Distill's identification data. "
    "The original photo remains in the recruiter's Google Drive."
)


def now():
    return datetime.now(timezone.utc)


async def link_reference(db, application_id, file_id):
    # A retained tombstone prevents reimports from undoing withdrawal or expiry.
    existing = await identity_row(db, application_id)
    if existing is None:
        db.add(CandidateIdentity(application_id=application_id, reference_file_id=file_id,
            expires_at=now() + timedelta(days=get_settings().identity_retention_days)))
    elif existing.consent == "pending" and not existing.reference_file_id and existing.expires_at > now():
        existing.reference_file_id = file_id


def clear_identity(row, consent):
    row.reference_file_id = None
    row.consent = consent
    row.status = "not_performed"
    for field in ("consent_version", "consent_at", "reason", "similarity", "threshold", "model_version",
                  "checked_at", "reviewed_by", "reviewed_at", "review_note"):
        setattr(row, field, None)
    row.attempts = 0


async def identity_row(db, application_id):
    # Lock the parent too: absent identity rows cannot themselves serialize enrollment.
    await db.scalar(select(CandidateApplication.id).where(
        CandidateApplication.id == application_id).with_for_update())
    row = await db.scalar(select(CandidateIdentity).where(
        CandidateIdentity.application_id == application_id).with_for_update())
    if row and row.expires_at <= now() and row.consent != "expired":
        clear_identity(row, "expired")
    return row


async def reset_for_invitation(db, application_id):
    row = await identity_row(db, application_id)
    if row and row.consent not in {"withdrawn", "expired"}:
        reference = row.reference_file_id
        clear_identity(row, "pending")
        row.reference_file_id = reference


def attempt_limit_reached(row):
    return bool(row and (row.attempts or 0) >= MAX_VERIFICATION_ATTEMPTS)


def manual_review_required(row):
    # A successful final comparison remains valid, but cannot be retaken.
    return bool(attempt_limit_reached(row) and row.status != "matched" and not row.reviewed_at)


def admission_ready(row):
    if manual_review_required(row):
        return False
    return bool(row and (row.reviewed_at or row.consent in {"declined", "withdrawn", "expired"}
        or (row.consent == "accepted" and row.consent_version == CONSENT_VERSION and row.checked_at
            and row.status in {"matched", "not_identified", "unable_to_verify"})))


def is_admitted(session, invitation, row):
    """Admission must follow this invitation's verification or manual decision."""
    if not (session and session.status == "in_progress" and session.candidate_admitted_at
            and session.admitted_invitation_hash == invitation.token_hash and admission_ready(row)):
        return False
    if row.consent in {"withdrawn", "expired"}:
        return True
    decisions = [row.reviewed_at]
    if row.consent == "declined":
        decisions.append(row.consent_at)
    elif row.consent == "accepted" and row.consent_version == CONSENT_VERSION:
        decisions.append(row.checked_at)
    return any(decision and decision <= session.candidate_admitted_at for decision in decisions)


def public_identity(row, *, recruiter=False):
    result = {"status": row.status if row else "not_performed",
        "consent": row.consent if row else "pending",
        "has_reference": bool(row and row.reference_file_id),
        "reason": row.reason if row else "reference_missing",
        "checked_at": row.checked_at if row else None,
        "expires_at": row.expires_at if row else None,
        "reviewed_at": row.reviewed_at if row else None,
        "review_note": row.review_note if row and recruiter else None,
        "admission_ready": admission_ready(row),
        "attempts": (row.attempts or 0) if row else 0,
        "attempt_limit": MAX_VERIFICATION_ATTEMPTS,
        "attempt_limit_reached": attempt_limit_reached(row),
        "manual_review_required": manual_review_required(row)}
    if recruiter and row:
        result.update(similarity=row.similarity, threshold=row.threshold, model_version=row.model_version)
    return result


async def candidate_context(db, token, *, require_waiting=False):
    invitation = await valid_invitation(db, token)
    row = await identity_row(db, invitation.application_id)
    session = await db.scalar(select(InterviewSession).where(
        InterviewSession.application_id == invitation.application_id).execution_options(populate_existing=True))
    if require_waiting and session and (session.status in {"closing", "completed", "cancelled"}
                                      or is_admitted(session, invitation, row)):
        raise HTTPException(409, "Face verification is available before admission. Ask your recruiter for manual review.")
    # Do not let a later consent/withdrawal turn a historical grant into admission.
    if session and session.candidate_admitted_at and not is_admitted(session, invitation, row):
        session.candidate_admitted_at = None
        session.admitted_invitation_hash = None
        session.version += 1
    return invitation, row


async def candidate_identity(token):
    async with SessionFactory() as db:
        _, row = await candidate_context(db, token)
        if row and row.consent == "accepted" and row.consent_version != CONSENT_VERSION:
            reference = row.reference_file_id
            clear_identity(row, "pending")
            row.reference_file_id = reference
        result = public_identity(row)
        result.update(consent_text=CONSENT_TEXT, consent_version=CONSENT_VERSION,
                      available=face_matching.available(), retention_days=get_settings().identity_retention_days)
        await db.commit()
        return result


async def set_consent(token, choice, version):
    if choice == "accepted" and version != CONSENT_VERSION:
        raise HTTPException(409, "Read and accept the current consent explanation.")
    async with SessionFactory() as db:
        _, row = await candidate_context(db, token, require_waiting=choice == "accepted")
        if row is None:
            raise HTTPException(409, "No reference photo is linked. Ask your recruiter for manual review.")
        if choice == "withdrawn":
            clear_identity(row, "withdrawn")
        else:
            if not row.reference_file_id:
                raise HTTPException(409, "The reference photo has expired or was deleted. Use manual review.")
            row.consent, row.consent_at, row.consent_version = choice, now(), CONSENT_VERSION
            if choice == "declined":
                row.status, row.similarity, row.threshold, row.model_version = "not_performed", None, None, None
                row.checked_at = row.reason = row.reviewed_by = row.reviewed_at = row.review_note = None
        await db.commit()
        return public_identity(row)


async def verify_frame(token, frame):
    async with SessionFactory() as db:
        invitation, row = await candidate_context(db, token, require_waiting=True)
        if row is None or not row.reference_file_id or row.consent != "accepted" or row.consent_version != CONSENT_VERSION:
            raise HTTPException(409, "A reference photo and explicit consent are required before comparison.")
        if row.attempts >= MAX_VERIFICATION_ATTEMPTS:
            raise HTTPException(429, f"The {MAX_VERIFICATION_ATTEMPTS}-attempt limit has been reached. Ask your recruiter for manual review.")
        if not face_matching.available():
            raise HTTPException(503, "Face verification is unavailable. Ask your recruiter for manual review.")
        if row.checked_at and (now() - row.checked_at).total_seconds() < 3:
            raise HTTPException(429, "Wait a few seconds before retrying.")
        row.attempts += 1
        row.checked_at = now()
        row.status, row.reason = "unable_to_verify", None
        row.similarity = row.threshold = row.model_version = None
        row.reviewed_by = row.reviewed_at = row.review_note = None
        try:
            reference = await run_in_threadpool(face_matching.download_reference, row.reference_file_id)
            result = await run_in_threadpool(face_matching.compare_faces, reference, frame)
            for field, value in result.items():
                setattr(row, field, value)
        except face_matching.FaceInputError as exc:
            row.reason = str(exc)
        except (httpx.HTTPError, RuntimeError, ImportError, ValueError):
            row.reason = "service_unavailable"
        # Revalidate after all remote I/O, including failed comparisons.
        await valid_invitation(db, token)
        session = await db.scalar(select(InterviewSession).where(
            InterviewSession.application_id == invitation.application_id).execution_options(populate_existing=True))
        if session and (is_admitted(session, invitation, row) or session.status in {"closing", "completed", "cancelled"}):
            raise HTTPException(409, "The waiting room has closed. Use manual review.")
        await db.commit()
        return public_identity(row)


async def check_frame(token, frame):
    async with SessionFactory() as db:
        _, row = await candidate_context(db, token, require_waiting=True)
        if row is None or not row.reference_file_id or row.consent != "accepted" or row.consent_version != CONSENT_VERSION:
            raise HTTPException(409, "Explicit consent is required before checking the camera preview.")
        if row.attempts >= MAX_VERIFICATION_ATTEMPTS:
            raise HTTPException(429, f"The {MAX_VERIFICATION_ATTEMPTS}-attempt limit has been reached. Ask your recruiter for manual review.")
        try:
            count = await run_in_threadpool(face_matching.count_faces, frame)
        except face_matching.FaceInputError as exc:
            return {"face_count": None, "reason": str(exc)}
        except (RuntimeError, ImportError, ValueError):
            raise HTTPException(503, "Camera checking is unavailable. Ask your recruiter for manual review.") from None
        await valid_invitation(db, token)
        await db.commit()
        return {"face_count": count, "reason": "multiple_faces" if count > 1 else "no_face" if count == 0 else None}


async def recruiter_identity(job_id, application_id, recruiter_id, *, review_note=None, delete=False):
    async with SessionFactory() as db:
        await owned_application(db, job_id, application_id, recruiter_id)
        row = await identity_row(db, application_id)
        if row is None and (delete or review_note is not None):
            row = CandidateIdentity(application_id=application_id,
                expires_at=now() + timedelta(days=get_settings().identity_retention_days))
            db.add(row)
            await db.flush()
        if delete:
            clear_identity(row, "withdrawn")
        elif review_note is not None:
            if row.consent in {"withdrawn", "expired"}:
                raise HTTPException(409, "Identification data was deleted or expired.")
            row.reviewed_by, row.reviewed_at, row.review_note = recruiter_id, now(), review_note
        await db.commit()
        return public_identity(row, recruiter=True)


async def purge_expired():
    """Run periodically; keep only an application tombstone to prevent re-enrollment."""
    async with SessionFactory() as db:
        await db.execute(update(CandidateIdentity).where(CandidateIdentity.expires_at <= now(),
            CandidateIdentity.consent != "expired").values(reference_file_id=None, consent="expired",
            status="not_performed", consent_version=None, consent_at=None, reason=None, similarity=None,
            threshold=None, model_version=None, checked_at=None, attempts=0,
            reviewed_by=None, reviewed_at=None, review_note=None))
        await db.commit()
