from typing import Annotated
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response
from app.auth import require_recruiter
from app.models.user import User
from app.services.interviews import assessment_runs as service
from app.services.interviews import consolidation_runs
from app.services.interviews import full_report
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/assessments", tags=["Assessments"])
Recruiter = Annotated[User, Depends(require_recruiter)]


@router.get("/jobs/{job_id}")
async def workspace(job_id: UUID, recruiter: Recruiter, response: Response, offset: int = Query(0, ge=0)):
    response.headers["Cache-Control"] = "no-store"
    return await service.workspace(job_id, recruiter.id, offset)


@router.post("/jobs/{job_id}/f5", status_code=202)
async def start(job_id: UUID, request: service.StartAssessment, recruiter: Recruiter):
    return await service.start_run(job_id, recruiter.id, request)


@router.get("/runs/{run_id}")
async def status(run_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await service.run_status(run_id, recruiter.id)


@router.get("/runs/{run_id}/result")
async def result(run_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await service.run_result(run_id, recruiter.id)


@router.post("/runs/{run_id}/retry", status_code=202)
async def retry(run_id: UUID, recruiter: Recruiter):
    return await service.retry_run(run_id, recruiter.id)


@router.post("/runs/{run_id}/f6", status_code=202)
async def consolidate(run_id: UUID, request: consolidation_runs.StartConsolidation, recruiter: Recruiter):
    return await consolidation_runs.start(run_id, recruiter.id, request)


@router.post("/runs/{run_id}/recover", status_code=202)
async def recover(run_id: UUID, recruiter: Recruiter):
    return await service.recover_run(run_id, recruiter.id)


@router.get("/runs/{run_id}/report")
async def report(run_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await full_report.document(run_id, recruiter.id)


@router.get("/runs/{run_id}/report.pdf")
async def pdf_report(run_id: UUID, recruiter: Recruiter):
    from app.services.interviews.report_pdf import render
    document = await full_report.document(run_id, recruiter.id)
    content = await run_in_threadpool(render, document)
    return Response(content, media_type="application/pdf", headers={"Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{document["filename"]}"',
        "X-Content-Type-Options": "nosniff"})
