from typing import Annotated, Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Query, Response
from app.auth import require_recruiter
from app.models.user import User
from app.services.interviews import assessment_runs as service
from app.services.interviews import consolidation_runs
from app.services.interviews import full_report
from app.services.interviews import scoring_settings
from app.services.interviews import post_interview_runs
from starlette.concurrency import run_in_threadpool

router = APIRouter(prefix="/assessments", tags=["Assessments"])
Recruiter = Annotated[User, Depends(require_recruiter)]


@router.get("/jobs/{job_id}/scoring-settings")
async def job_scoring_settings(job_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await scoring_settings.get(job_id, recruiter.id)


@router.put("/jobs/{job_id}/scoring-settings")
async def save_job_scoring_settings(job_id: UUID, request: scoring_settings.SaveSettings,
        recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await scoring_settings.save(job_id, recruiter.id, request)


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


@router.get("/runs/{run_id}/post-interview")
async def post_interview_status(run_id: UUID, recruiter: Recruiter, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return await post_interview_runs.workspace(run_id, recruiter.id)


@router.post("/runs/{run_id}/post-interview", status_code=202)
async def start_post_interview(run_id: UUID, request: post_interview_runs.StartAnalysis, recruiter: Recruiter):
    return await post_interview_runs.start(run_id, recruiter.id, request)


@router.post("/runs/{run_id}/findings/{finding_id}/reviews", status_code=201)
async def review_claim(run_id: UUID, finding_id: str, request: post_interview_runs.ReviewFinding, recruiter: Recruiter):
    return await post_interview_runs.review(run_id, finding_id, recruiter.id, request)


@router.post("/runs/{run_id}/recover", status_code=202)
async def recover(run_id: UUID, recruiter: Recruiter):
    return await service.recover_run(run_id, recruiter.id)


@router.get("/runs/{run_id}/report")
async def report(run_id: UUID, recruiter: Recruiter, response: Response,
        report_type: Literal["recruiter", "technical"] = "recruiter", include_evidence: bool = False,
        post_interview_run_ids: list[UUID] = Query(default=[], max_length=3)):
    response.headers["Cache-Control"] = "no-store"
    return await full_report.document(run_id, recruiter.id, report_type, include_evidence, post_interview_run_ids)


@router.get("/runs/{run_id}/report.pdf")
async def pdf_report(run_id: UUID, recruiter: Recruiter,
        report_type: Literal["recruiter", "technical"] = "recruiter", include_evidence: bool = False,
        post_interview_run_ids: list[UUID] = Query(default=[], max_length=3)):
    from app.services.interviews.report_pdf import render
    document = await full_report.document(run_id, recruiter.id, report_type, include_evidence, post_interview_run_ids)
    content = await run_in_threadpool(render, document)
    return Response(content, media_type="application/pdf", headers={"Cache-Control": "no-store",
        "Content-Disposition": f'attachment; filename="{document["filename"]}"',
        "X-Content-Type-Options": "nosniff"})
