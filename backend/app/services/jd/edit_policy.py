"""Shared edit tokens and publication locks for saved job descriptions."""

import hashlib
import json

from fastapi import HTTPException
from sqlalchemy import select

from app.models.jd import DiscordJobPost
from app.services.jd.screening_profiles import public_jd_narrative


LOCKED_POST_STATUSES = {"sending", "posted", "unknown"}


def document_edit_version(document) -> str:
    # Include prose as well as criteria: two editors changing only the prose
    # must still detect a conflict without relying on timestamp precision.
    data = json.dumps({
        "content": document.content,
        "profile": document.screening_profile,
        "profile_version": document.profile_version,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(data.encode()).hexdigest()


async def posting_status(db, document_id):
    return await db.scalar(select(DiscordJobPost.status).where(
        DiscordJobPost.job_description_id == document_id,
    ))


async def require_editable(db, document):
    status = await posting_status(db, document.id)
    if status in LOCKED_POST_STATUSES:
        raise HTTPException(409, "This job is posted or posting is pending. Its description and criteria are locked.")


async def description_details(db, document) -> dict:
    status = await posting_status(db, document.id)
    return {
        "id": str(document.id), "number": document.number,
        "title": document.title, "created_at": document.created_at,
        "conversation_id": str(document.conversation_id),
        "content": document.content, "screening_profile": document.screening_profile,
        "profile_version": document.profile_version, "google_form_url": document.google_form_url,
        "editable_content": public_jd_narrative(document.content),
        "edit_version": document_edit_version(document),
        "editing_locked": status in LOCKED_POST_STATUSES,
        "posting_status": status,
    }
