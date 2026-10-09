"""Durable entry points; wrappers supply authenticated recruiter identity."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from time import monotonic
from uuid import UUID
import httpx
import jwt
from fastapi import HTTPException
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool
from app.core.config import get_settings
from app.core.security import ALGORITHM
from app.db.session import SessionFactory
from app.models.cv import Candidate, CandidateApplication, CVDocument, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.models.jd import JDConversation, JobDescription
from app.services.cv.indexing import INDEX_VERSION, digest
from app.services.cv.matching import MATCH_PROMPT, MATCH_VERSION, RequirementMatch
from app.services.cv.parsing import local_versions, verify_integrity
from app.integrations.cv_model import model_version, provider, model_name, context_tokens
from app.services.cv.scoring import RUBRIC, calculate_scores, rank_assessments, validate_scoring_profile

WORKER_LOCK = 73101984023
_versions_cache = None
_versions_lock = asyncio.Lock()


async def owned_job(db, job_id, recruiter_id):
    job = await db.scalar(select(JobDescription).join(JDConversation).where(JobDescription.id == job_id,
        JDConversation.recruiter_id == recruiter_id))
    if not job:
        raise HTTPException(404, "Job not found.")
    return job


async def owned_application(db, job_id, application_id, recruiter_id):
    await owned_job(db, job_id, recruiter_id)
    application = await db.scalar(select(CandidateApplication).join(Candidate).where(CandidateApplication.id == application_id,
        CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id))
    if not application:
        raise HTTPException(404, "Application not found.")
    return application


async def processing_versions():
    global _versions_cache
    settings = get_settings()
    config = (settings.ollama_base_url, provider(), model_name(), settings.embedding_model,
              context_tokens(), settings.cv_model_output_tokens, settings.cv_ocr_language)
    async with _versions_lock:
        if _versions_cache and _versions_cache[0] == config and monotonic() - _versions_cache[1] < 30:
            return _versions_cache[2]
        local = await run_in_threadpool(local_versions)
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
            response.raise_for_status()
        models = {m["name"]: m["digest"] for m in response.json().get("models", [])}
        if settings.embedding_model not in models:
            raise ValueError("SCREENING_MODEL_NOT_INSTALLED")
        local.update(await model_version())
        versions = {"parsing": local, "matching": MATCH_VERSION,
            "match_prompt_hash": sha256(MATCH_PROMPT.encode()).hexdigest(), "match_schema": digest(RequirementMatch.model_json_schema()),
            "indexing": {"chunk_version": INDEX_VERSION, "embedding_model": settings.embedding_model,
                         "embedding_digest": models[settings.embedding_model]},
            "context_tokens": context_tokens(), "output_tokens": settings.cv_model_output_tokens}
        _versions_cache = (config, monotonic(), versions)
        return versions


async def manifest(db, job_id, recruiter_id):
    applications = (await db.execute(select(CandidateApplication, Candidate).join(Candidate).where(
        CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id).order_by(CandidateApplication.id))).all()
    output = []
    for application, candidate in applications:
        documents = list(await db.scalars(select(CVDocument).where(CVDocument.application_id == application.id,
            CVDocument.job_description_id == job_id).order_by(CVDocument.id)))
        files = []
        for document in documents:
            version = await db.scalar(select(CVFileVersion).where(CVFileVersion.cv_document_id == document.id,
                CVFileVersion.status == "downloaded").order_by(CVFileVersion.created_at.desc(), CVFileVersion.id.desc()).limit(1))
            if version:
                files.append({"id": str(version.id), "sha256": version.file_sha256, "document_id": str(document.id)})
        output.append({"application_id": str(application.id), "files": files,
            "sheet_identity": {"name": candidate.full_name, "email": candidate.email}})
    return output


async def inputs_for(db, job, recruiter_id):
    return {"profile": job.screening_profile, "profile_version": job.profile_version, "rubric": RUBRIC,
            "versions": await processing_versions(), "applications": await manifest(db, job.id, recruiter_id)}


def run_public(run, assessments):
    return {"id": str(run.id), "job_id": str(run.job_id), "status": run.status, "attempt": run.attempt,
        "total": len(assessments), "finished": sum(a.status in {"completed", "failed", "needs_review"} for a in assessments),
        "completed": sum(a.status == "completed" for a in assessments), "warnings": run.warnings,
        "error_code": run.error_code, "profile_version": run.inputs.get("profile_version"),
        "rubric_version": run.inputs.get("rubric", {}).get("version"), "updated_at": run.updated_at.isoformat()}


async def start_job_screening(job_id, recruiter_id):
    async with SessionFactory() as db:
        job = await owned_job(db, job_id, recruiter_id)
        await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": job_id.int % (2**63 - 1)})
        try:
            inputs = await inputs_for(db, job, recruiter_id)
        except Exception:
            raise HTTPException(503, "Local screening models are unavailable. Check Ollama and retry.") from None
        key = digest(inputs)
        run = await db.scalar(select(ScreeningRun).where(ScreeningRun.job_id == job_id,
            ScreeningRun.input_key == key, ScreeningRun.status != "outdated").order_by(ScreeningRun.created_at.desc()).limit(1))
        if run:
            assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == run.id)))
            return run_public(run, assessments)
        for old in await db.scalars(select(ScreeningRun).where(ScreeningRun.job_id == job_id, ScreeningRun.status != "outdated")):
            old.status = "outdated"
        error = None
        try:
            validate_scoring_profile(inputs["profile"], inputs["profile_version"])
        except (ValueError, TypeError):
            error = "PROFILE_CORRECTION_NEEDED"
        run = ScreeningRun(job_id=job_id, recruiter_id=recruiter_id, input_key=key, inputs=inputs,
            status="correction_needed" if error else "queued", error_code=error)
        db.add(run)
        await db.flush()
        assessments = []
        for item in inputs["applications"]:
            assessment = CVAssessment(run_id=run.id, application_id=UUID(item["application_id"]),
                file_version_ids=[f["id"] for f in item["files"]])
            db.add(assessment)
            assessments.append(assessment)
        await db.commit()
        return run_public(run, assessments)


async def resume_run(run_id, recruiter_id):
    async with SessionFactory() as db:
        run = await db.scalar(select(ScreeningRun).where(ScreeningRun.id == run_id, ScreeningRun.recruiter_id == recruiter_id).with_for_update())
        if not run:
            raise HTTPException(404, "Run not found.")
        job = await owned_job(db, run.job_id, recruiter_id)
        try:
            current = await inputs_for(db, job, recruiter_id)
        except Exception:
            raise HTTPException(503, "Local screening models are unavailable.") from None
        if digest(current) != run.input_key or run.status in {"outdated", "correction_needed"}:
            raise HTTPException(409, "Screening inputs changed or need correction. Start a new run.")
        assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == run.id)))
        if run.status not in {"queued", "running"}:
            for assessment in assessments:
                if assessment.status != "completed":
                    assessment.status, assessment.error_code = "pending", None
                    assessment.attempt += 1
            run.status, run.error_code, run.attempt = "queued", None, run.attempt + 1
            await db.commit()
        return run_public(run, assessments)


async def run_status(run_id, recruiter_id):
    async with SessionFactory() as db:
        run = await db.scalar(select(ScreeningRun).where(ScreeningRun.id == run_id, ScreeningRun.recruiter_id == recruiter_id))
        if not run:
            raise HTTPException(404, "Run not found.")
        assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == run.id).order_by(CVAssessment.application_id)))
        return {**run_public(run, assessments), "applications": [{"application_id": str(a.application_id), "status": a.status,
            "warnings": a.warnings, "error_code": a.error_code} for a in assessments]}


async def get_screening_results(job_id, recruiter_id):
    async with SessionFactory() as db:
        job = await owned_job(db, job_id, recruiter_id)
        runs = list(await db.scalars(select(ScreeningRun).where(ScreeningRun.job_id == job_id,
            ScreeningRun.recruiter_id == recruiter_id).order_by(ScreeningRun.created_at.desc())))
        current_key = None
        warnings = []
        try:
            current_key = digest(await inputs_for(db, job, recruiter_id))
        except Exception:
            warnings = ["CURRENT_MODEL_VERSIONS_UNAVAILABLE_RANKING_WITHHELD"]
        results, history = [], []
        for position, run in enumerate(runs):
            outdated = current_key is not None and run.input_key != current_key
            if outdated:
                run.status = "outdated"
            assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == run.id)))
            if position:
                history.append(run_public(run, assessments))
                continue
            for assessment in assessments:
                application = await db.get(CandidateApplication, assessment.application_id)
                candidate = await db.get(Candidate, application.candidate_id)
                valid_parses = True
                if assessment.status == "completed":
                    for parse_id in assessment.parse_result_ids:
                        parsed = await db.get(CVParseResult, UUID(parse_id))
                        if not parsed or parsed.status != "completed":
                            valid_parses = False
                            break
                        try:
                            version = await db.get(CVFileVersion, parsed.file_version_id)
                            await run_in_threadpool(verify_integrity, version)
                        except Exception:
                            valid_parses = False
                            break
                    if not valid_parses:
                        run.status = "outdated"
                results.append({"id": str(assessment.id), "application_id": str(assessment.application_id), "name": candidate.full_name,
                    "status": assessment.status, "outdated": outdated or run.status == "outdated" or not valid_parses, "file_version_ids": assessment.file_version_ids,
                    "parse_result_ids": assessment.parse_result_ids, "index_ids": assessment.index_ids,
                    "score": assessment.score, "matches": assessment.matches, "warnings": assessment.warnings,
                    "error_code": assessment.error_code, "reused_from": assessment.reused_from})
        await db.commit()
        ranked = rank_assessments([r for r in results if r["status"] == "completed" and not r["outdated"] and current_key])
        ranked_ids = {r["id"] for r in ranked}
        return {"job_id": str(job_id), "run": run_public(runs[0], list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == runs[0].id)))) if runs else None,
            "profile": runs[0].inputs.get("profile") if runs else job.screening_profile,
            "versions": runs[0].inputs.get("versions") if runs else None, "rubric": RUBRIC,
            "ranked": ranked, "incomplete": [r for r in results if r["id"] not in ranked_ids], "history": history, "warnings": warnings}


def evidence_token(recruiter_id, assessment_id):
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": str(recruiter_id), "iat": now, "exp": now + timedelta(minutes=10),
        "cv_evidence_assessment": str(assessment_id)}, get_settings().jwt_secret_key.get_secret_value(), algorithm=ALGORITHM)


async def retrieve_through_mcp(job_id, application_id, query, recruiter_id, assessment_id):
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    try:
        async with asyncio.timeout(180):
            async with httpx.AsyncClient(headers={"Authorization": "Bearer " + evidence_token(recruiter_id, assessment_id)}, timeout=180) as client:
                async with streamable_http_client(get_settings().cv_mcp_url, http_client=client) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool("search_cv_evidence", {"job_id": str(job_id), "application_id": str(application_id), "query": query})
    except Exception:
        raise ValueError("MCP_EVIDENCE_RETRIEVAL_FAILED") from None
    if result.isError or not result.structuredContent:
        raise ValueError("MCP_EVIDENCE_RETRIEVAL_FAILED")
    content = result.structuredContent
    if content.get("assessment_id") != str(assessment_id) or content.get("application_id") != str(application_id):
        raise ValueError("MCP_EVIDENCE_SCOPE_INVALID")
    return content
