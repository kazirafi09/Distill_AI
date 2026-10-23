from datetime import datetime
from typing import Annotated
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth import require_recruiter
from app.core.config import get_settings
from app.db.session import get_db
from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion, CVSource, CVSourceCheck
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.integrations.google_drive import list_folder_files
from app.services.cv.ingestion import fetch_cv_source
from app.services.cv.sheet_import import response_columns, source_lock_key
from app.integrations.google_sheets import read_response_sheet

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
    spreadsheet_id: str | None
    sheet_tab_id: int | None
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


@router.get("/source-checks")
async def list_source_checks(db: DatabaseSession, recruiter: Recruiter,
    offset: Annotated[int, Query(ge=0)] = 0, limit: Annotated[int, Query(ge=1, le=100)] = 100):
    rows = (await db.execute(select(CVSource, CVSourceCheck)
        .outerjoin(CVSourceCheck, CVSourceCheck.source_id == CVSource.id)
        .join(JobDescription, CVSource.job_description_id == JobDescription.id)
        .join(JDConversation, JobDescription.conversation_id == JDConversation.id)
        .where(JDConversation.recruiter_id == recruiter.id)
        .order_by(CVSource.id).offset(offset).limit(limit))).all()
    return [{"source_id": str(source.id), "job_description_id": str(source.job_description_id),
             "new_cv_count": len(check.pending_file_ids) if check else 0,
             "last_checked_at": check.last_checked_at if check else None,
             "last_success_at": check.last_success_at if check else None,
             "error_code": check.error_code if check else None} for source, check in rows]


@router.post("/sources/{source_id}/fetch")
async def fetch_source(
    source_id: UUID,
    recruiter: Recruiter,
):
    return await fetch_cv_source(source_id, recruiter.id)


class CVSheetMappingRequest(BaseModel):
    spreadsheet_id: str = Field(min_length=1, max_length=255, pattern=r"^[A-Za-z0-9_-]+$")
    sheet_tab_id: int = Field(ge=0, le=2147483647)


@router.put("/sources/{source_id}/sheet", response_model=CVSourceResponse)
async def link_response_sheet(
    source_id: UUID,
    payload: CVSheetMappingRequest,
    db: DatabaseSession,
    recruiter: Recruiter,
):
    source = await db.scalar(
        select(CVSource).join(JobDescription, CVSource.job_description_id == JobDescription.id)
        .join(JDConversation, JobDescription.conversation_id == JDConversation.id)
        .where(CVSource.id == source_id, JDConversation.recruiter_id == recruiter.id)
    )
    if source is None:
        raise HTTPException(status_code=404, detail="CV source not found.")
    # Share the fetch lock so its source mapping cannot change mid-fetch.
    locked = await db.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": source_lock_key(source_id)})
    if not locked:
        raise HTTPException(status_code=409, detail="This CV source is already being fetched or updated.")
    # Re-read after acquiring the lock: another mapping request might have committed.
    await db.refresh(source)
    if source.spreadsheet_id is not None:
        if (source.spreadsheet_id, source.sheet_tab_id) != (payload.spreadsheet_id, payload.sheet_tab_id):
            raise HTTPException(status_code=409, detail="This source already has a different response sheet.")
    try:
        sheet = await run_in_threadpool(read_response_sheet, payload.spreadsheet_id, payload.sheet_tab_id)
        response_columns(sheet["headers"])
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Could not read the response sheet. Check its ID and service-account access.") from None
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Google credentials are unavailable.") from None
    source.spreadsheet_id = payload.spreadsheet_id
    source.sheet_tab_id = payload.sheet_tab_id
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="This spreadsheet tab is already mapped to another job.") from None
    await db.refresh(source)
    return source


@router.get("/applications")
async def list_candidate_applications(
    db: DatabaseSession,
    recruiter: Recruiter,
    job_description_id: UUID | None = None,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    application_id: UUID | None = None,
):
    statement = (
        select(CandidateApplication, Candidate, JobDescription.title)
        .join(Candidate, CandidateApplication.candidate_id == Candidate.id)
        .join(JobDescription, CandidateApplication.job_description_id == JobDescription.id)
        .join(JDConversation, JobDescription.conversation_id == JDConversation.id)
        .where(JDConversation.recruiter_id == recruiter.id, Candidate.recruiter_id == recruiter.id)
        .order_by(CandidateApplication.created_at.desc(), CandidateApplication.id.desc())
        .offset(offset).limit(limit)
    )
    if job_description_id is not None:
        statement = statement.where(CandidateApplication.job_description_id == job_description_id)
    if application_id is not None:
        statement = statement.where(CandidateApplication.id == application_id)
    rows = (await db.execute(statement)).all()
    result = {}
    for application, candidate, title in rows:
        result[application.id] = {
            "id": application.id, "candidate_id": candidate.id,
            "full_name": candidate.full_name, "email": candidate.email,
            "job_description_id": application.job_description_id, "job_title": title,
            "source_id": application.source_id, "source_row_number": application.source_row_number,
            "submitted_at_text": application.submitted_at_text, "created_at": application.created_at,
            "cv_documents": [],
        }
    if result:
        latest_version_id = (
            select(CVFileVersion.id).where(CVFileVersion.cv_document_id == CVDocument.id)
            .order_by(CVFileVersion.created_at.desc(), CVFileVersion.id.desc()).limit(1)
            .correlate(CVDocument).scalar_subquery()
        )
        documents = await db.execute(
            select(CVDocument, CVFileVersion)
            .outerjoin(CVFileVersion, CVFileVersion.id == latest_version_id)
            .where(CVDocument.application_id.in_(result))
            .order_by(CVDocument.created_at, CVDocument.id)
        )
        for document, version in documents:
            result[document.application_id]["cv_documents"].append({
                "id": document.id, "drive_file_id": document.drive_file_id,
                "file_version_id": version.id if version else None,
                "original_filename": version.original_filename if version else None,
                "download_status": version.status if version else None,
            })
    return list(result.values())
