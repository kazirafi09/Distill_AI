from datetime import datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth import require_recruiter
from app.core.config import get_settings
from app.db.session import get_db
from app.models.cv import CVSource
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.services.google_drive import list_folder_files
from app.services.cv_ingestion import fetch_cv_source

router = APIRouter(prefix="/cv", tags=["CV Ingestion"])
DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
Recruiter = Annotated[User, Depends(require_recruiter)]


class CVSourceRequest(BaseModel):
    job_description_id: UUID
    drive_folder_id: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class CVSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    job_description_id: UUID
    drive_folder_id: str
    created_at: datetime
    updated_at: datetime


@router.post("/sources", response_model=CVSourceResponse)
async def link_cv_source(
    payload: CVSourceRequest,
    db: DatabaseSession,
    recruiter: Recruiter,
):
    job_id = await db.scalar(
        select(JobDescription.id)
        .join(
            JDConversation,
            JobDescription.conversation_id == JDConversation.id,
        )
        .where(
            JobDescription.id == payload.job_description_id,
            JDConversation.recruiter_id == recruiter.id,
        )
    )
    if job_id is None:
        raise HTTPException(status_code=404, detail="Job not found.")

    existing = await db.scalar(
        select(CVSource).where(CVSource.job_description_id == job_id)
    )
    if existing is not None:
        if existing.drive_folder_id == payload.drive_folder_id:
            return existing
        raise HTTPException(
            status_code=409,
            detail="This job already has a different CV folder.",
        )

    root_id = get_settings().google_drive_cv_root_folder_id
    if not root_id:
        raise HTTPException(
            status_code=503,
            detail="The CV parent folder is not configured.",
        )

    try:
        folders = await run_in_threadpool(list_folder_files, root_id)
    except httpx.HTTPError:
        raise HTTPException(
            status_code=502,
            detail="Could not read the CV parent folder from Google Drive.",
        ) from None
    except (RuntimeError, ValueError):
        raise HTTPException(
            status_code=503,
            detail="Check the Google Drive credentials and parent folder.",
        ) from None

    allowed_folder = any(
        item["id"] == payload.drive_folder_id
        and item["mimeType"] == "application/vnd.google-apps.folder"
        and not item.get("trashed")
        for item in folders
    )
    if not allowed_folder:
        raise HTTPException(
            status_code=422,
            detail="Choose a direct child folder of the configured CV folder.",
        )

    source = CVSource(
        job_description_id=job_id,
        drive_folder_id=payload.drive_folder_id,
    )
    db.add(source)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

        # Handle two identical link requests arriving together.
        existing = await db.scalar(
            select(CVSource).where(
                CVSource.job_description_id == job_id,
                CVSource.drive_folder_id == payload.drive_folder_id,
            )
        )
        if existing is not None:
            return existing

        raise HTTPException(
            status_code=409,
            detail="The job or folder already has a mapping, or the job changed.",
        ) from None

    await db.refresh(source)
    return source


@router.get("/sources", response_model=list[CVSourceResponse])
async def list_cv_sources(
    db: DatabaseSession,
    recruiter: Recruiter,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
):
    sources = await db.scalars(
        select(CVSource)
        .join(
            JobDescription,
            CVSource.job_description_id == JobDescription.id,
        )
        .join(
            JDConversation,
            JobDescription.conversation_id == JDConversation.id,
        )
        .where(JDConversation.recruiter_id == recruiter.id)
        .order_by(CVSource.created_at.desc(), CVSource.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return sources.all()


@router.post("/sources/{source_id}/fetch")
async def fetch_source(
    source_id: UUID,
    recruiter: Recruiter,
):
    return await fetch_cv_source(source_id, recruiter.id)