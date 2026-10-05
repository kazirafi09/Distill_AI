from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
)
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth import require_recruiter
from app.db.session import get_db
from app.models.jd_reference import JDReference
from app.models.user import User
from app.services.document_extraction import (
    MAX_FILE_BYTES,
    DocumentExtractionError,
    extract_document,
)


router = APIRouter(
    prefix="/jd/references",
    tags=["JD References"],
)

DatabaseSession = Annotated[AsyncSession, Depends(get_db)]
Recruiter = Annotated[User, Depends(require_recruiter)]
DocumentType = Literal["prior_jd", "company_policy"]


class ReferenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    file_type: str
    file_size_bytes: int
    document_type: DocumentType
    index_status: str
    error_code: str | None
    created_at: datetime
    updated_at: datetime


def duplicate_error() -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "DUPLICATE_REFERENCE",
            "message": "You have already uploaded this document.",
        },
    )


@router.post("", response_model=ReferenceResponse, status_code=201)
async def upload_reference(
    db: DatabaseSession,
    recruiter: Recruiter,
    file: Annotated[UploadFile, File()],
    document_type: Annotated[DocumentType, Form()] = "prior_jd",
):
    try:
        data = await file.read(MAX_FILE_BYTES + 1)

        extracted = await run_in_threadpool(
            extract_document,
            file.filename or "",
            data,
        )
    except DocumentExtractionError as exc:
        raise HTTPException(
            status_code=413 if exc.code == "FILE_TOO_LARGE" else 422,
            detail={
                "code": exc.code,
                "message": str(exc),
            },
        ) from None
    finally:
        await file.close()

    duplicate_query = select(JDReference.id).where(
        JDReference.recruiter_id == recruiter.id,
        JDReference.file_sha256 == extracted.file_sha256,
    )

    if await db.scalar(duplicate_query) is not None:
        raise duplicate_error()

    reference = JDReference(
        recruiter_id=recruiter.id,
        filename=extracted.filename,
        file_type=extracted.file_type,
        file_size_bytes=extracted.file_size_bytes,
        file_sha256=extracted.file_sha256,
        extracted_text=extracted.text,
        document_type=document_type,
        index_status="pending",
    )

    db.add(reference)

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

        if await db.scalar(duplicate_query) is not None:
            raise duplicate_error() from None

        raise

    await db.refresh(reference)
    return reference


@router.get("", response_model=list[ReferenceResponse])
async def list_references(
    db: DatabaseSession,
    recruiter: Recruiter,
    offset: int = 0,
    limit: int = 50,
):
    if offset < 0 or not 1 <= limit <= 100:
        raise HTTPException(
            status_code=422,
            detail="offset must be nonnegative; limit must be 1–100.",
        )

    result = await db.scalars(
        select(JDReference)
        .where(JDReference.recruiter_id == recruiter.id)
        .order_by(JDReference.created_at.desc(), JDReference.id.desc())
        .offset(offset)
        .limit(limit)
    )

    return result.all()