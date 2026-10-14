"""Recruiter-only question bank upload and management endpoints."""

import logging
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth import require_recruiter
from app.db.session import get_db
from app.models.question_bank import QuestionBank
from app.models.user import User
from app.services.documents.extraction import MAX_FILE_BYTES
from app.services.question_banks.ingestion import upload_question_bank
from app.services.question_banks.indexing import index_question_bank


router = APIRouter(
    prefix="/question-banks",
    tags=["Question Banks"],
)
logger = logging.getLogger("distill_ai.question_banks")

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
Recruiter = Annotated[User, Depends(require_recruiter)]


class QuestionBankResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    file_type: str
    file_size_bytes: int
    status: Literal["pending", "processing", "ready", "failed"]
    error_code: str | None
    warnings: list[str]
    chunk_count: int
    is_archived: bool
    created_at: datetime
    updated_at: datetime


async def owned_bank(
    db: AsyncSession,
    recruiter_id: UUID,
    bank_id: UUID,
) -> QuestionBank:
    bank = await db.scalar(
        select(QuestionBank)
        .where(
            QuestionBank.id == bank_id,
            QuestionBank.recruiter_id == recruiter_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if bank is None:
        raise HTTPException(404, "Question bank not found.")

    return bank


async def index_saved_bank(
    db: AsyncSession,
    recruiter_id: UUID,
    bank_id: UUID,
) -> QuestionBank:
    # Serialize indexing and archive operations for this bank.
    bank = await owned_bank(db, recruiter_id, bank_id)

    if bank.is_archived:
        raise HTTPException(409, "Archived question banks cannot be indexed.")

    if (
        not bank.extracted_text
        or not bank.sources
        or bank.error_code == "TEXT_UNUSABLE_OR_INCOMPLETE"
    ):
        raise HTTPException(
            409, "Document extraction must succeed before indexing.",
        )

    bank.status = "processing"
    bank.error_code = None
    await db.flush()

    try:
        count, version = await run_in_threadpool(index_question_bank, bank)
    except Exception as exc:
        logger.error(
            "Question bank indexing failed | bank_id=%s | error_type=%s",
            bank.id,
            type(exc).__name__,
        )
        bank.status = "failed"
        bank.error_code = "QUESTION_BANK_INDEX_FAILED"
        bank.chunk_count = 0
        bank.index_version = None
    else:
        bank.status = "ready"
        bank.chunk_count = count
        bank.index_version = version

    await db.commit()
    await db.refresh(bank)
    return bank


@router.post(
    "",
    response_model=QuestionBankResponse,
    status_code=201,
)
async def upload_bank(
    db: DatabaseSession,
    recruiter: Recruiter,
    file: Annotated[UploadFile, File()],
):
    try:
        data = await file.read(MAX_FILE_BYTES + 1)
        filename = file.filename or ""
    finally:
        await file.close()

    bank = await upload_question_bank(db, recruiter.id, filename, data)

    if bank.status == "pending":
        bank = await index_saved_bank(db, recruiter.id, bank.id)

    return bank


@router.get("", response_model=list[QuestionBankResponse])
async def list_banks(
    db: DatabaseSession,
    recruiter: Recruiter,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
):
    return list(await db.scalars(
        select(QuestionBank)
        .where(QuestionBank.recruiter_id == recruiter.id)
        .order_by(QuestionBank.created_at.desc(), QuestionBank.id.desc())
        .offset(offset)
        .limit(limit)
    ))


@router.post("/{bank_id}/index", response_model=QuestionBankResponse)
async def retry_index(
    bank_id: UUID,
    db: DatabaseSession,
    recruiter: Recruiter,
):
    return await index_saved_bank(db, recruiter.id, bank_id)


@router.post("/{bank_id}/archive", response_model=QuestionBankResponse)
async def archive_bank(
    bank_id: UUID,
    db: DatabaseSession,
    recruiter: Recruiter,
):
    bank = await owned_bank(db, recruiter.id, bank_id)
    bank.is_archived = True

    await db.commit()
    await db.refresh(bank)
    return bank
