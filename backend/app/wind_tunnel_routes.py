"""Owned F11 background batches and explicit reviewed Apply."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.auth import DatabaseSession, require_recruiter
from app.models.user import User
from app.services.recruitment_access import owned_job
from app.services.wind_tunnel import queue, review, storage

router = APIRouter(prefix="/jd/descriptions/{job_id}/suggestions", tags=["Job suggestions"])
Recruiter = Annotated[User, Depends(require_recruiter)]


class More(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    suggestion_ids: list[UUID] = Field(min_length=1, max_length=3)
    edit_version: str = Field(min_length=64, max_length=64)


class Apply(Selection):
    review_token: str = Field(min_length=64, max_length=64)
    criteria_reviewed: bool = False


@router.get("")
async def get_history(job_id: UUID, user: Recruiter, db: DatabaseSession):
    return await review.history(db, job_id, user.id)


@router.post("/more", status_code=202)
async def more(job_id: UUID, body: More, user: Recruiter, db: DatabaseSession):
    job = await owned_job(db, job_id, user.id)
    task = await queue.enqueue(db, job, user.id, request_id=body.request_id)
    await db.commit()
    return {"id": task.id, "status": task.status}


@router.post("/batches/{task_id}/retry", status_code=202)
async def retry(job_id: UUID, task_id: UUID, user: Recruiter, db: DatabaseSession):
    task = await queue.retry(db, job_id, task_id, user.id)
    await db.commit()
    return {"id": task.id, "status": task.status}


@router.post("/batches/{task_id}/cancel")
async def cancel(job_id: UUID, task_id: UUID, user: Recruiter, db: DatabaseSession):
    task = await queue.cancel(db, job_id, task_id, user.id)
    await db.commit()
    return {"id": task.id, "status": task.status}


@router.post("/preview")
async def preview(job_id: UUID, body: Selection, user: Recruiter, db: DatabaseSession):
    prepared, _ = await review.preview(db, job_id, user.id, body.suggestion_ids, body.edit_version)
    return prepared


@router.post("/apply")
async def apply(job_id: UUID, body: Apply, request: Request, user: Recruiter, db: DatabaseSession):
    return await review.apply(db, request.app.state.jd_graph, job_id, user.id,
        body.suggestion_ids, body.edit_version, body.review_token, body.criteria_reviewed)


@router.get("/runs/{run_id}/documents/{document_id}")
async def document(job_id: UUID, run_id: UUID, document_id: UUID, user: Recruiter, db: DatabaseSession):
    await owned_job(db, job_id, user.id)
    run = await storage.owned_run(db, run_id, user.id)
    if run.job_id != job_id:
        from fastapi import HTTPException
        raise HTTPException(404, "Synthetic document not found.")
    _, source, path = await storage.owned_document(db, run_id, document_id, user.id)
    return FileResponse(path, filename=f"synthetic-{document_id}{source.suffix}")
