"""Owned CV claim review, performance and process-quality assessment runs."""
from dataclasses import asdict
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.integrations.cv_model import ModelExecution
from app.models.assessments import AssessmentRun, AssessmentResult, AssessmentFindingReview
from app.services.recruitment_access import lock_job
from app.services.interviews import analysis, post_interview_analysis as service, scoring_settings
from app.services.interviews.assessment_runs import owned_run, public_run
from app.services.interviews.consolidation_runs import real_cv, synthetic_cv


class StartAnalysis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    features: list[Literal["cv_claim_review", "interview_performance", "interview_quality"]] = Field(min_length=1, max_length=3)
    confirm_f5_review: StrictBool = False


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["reviewed", "clarification"]
    note: str = Field(default="", max_length=2000)


async def bundle(db, parent):
    if parent.kind != "f5" or parent.status != "completed":
        raise HTTPException(409, "Post-interview analysis requires a completed F5 result.")
    saved = await db.get(AssessmentResult, parent.id)
    f5 = saved.payload if saved else {}
    if (f5.get("status") != ("completed_synthetic" if parent.synthetic else "completed")
            or f5.get("artifact_sha256") != analysis.digest({k: v for k, v in f5.items() if k != "artifact_sha256"})
            or f5.get("synthetic") is not parent.synthetic or not f5.get("results")):
        raise HTTPException(409, "The saved F5 source failed its readiness or integrity check.")
    result = {key: parent.inputs[key] for key in ("criteria", "profile_version", "profile_snapshot", "prepared")}
    result.update(parent_run_id=str(parent.id), f5=f5, f5_input_key=parent.input_key,
                  references=f5.get("references", parent.inputs.get("references", [])))
    if parent.inputs.get("test_application_id"):
        result["test_application_id"] = parent.inputs["test_application_id"]
    return result


async def cv_bundle(db, parent):
    cv = synthetic_cv(parent) if parent.synthetic and not parent.inputs.get("test_application_id") else await real_cv(db, parent)
    if not cv.get("documents") or any(not doc.get("text", "").strip() or not doc.get("sources") for doc in cv["documents"]):
        raise HTTPException(409, "Pinned CV source text and page spans are required for claim review.")
    return cv


def public_review(row):
    return {"id": str(row.id), "finding_id": row.finding_id, "author_id": str(row.recruiter_id),
            "created_at": row.created_at.isoformat(), **row.payload}


async def workspace(parent_id, recruiter_id):
    async with SessionFactory() as db:
        parent = await owned_run(db, parent_id, recruiter_id)
        blockers = dict.fromkeys(service.KINDS)
        try:
            await bundle(db, parent)
        except HTTPException as error:
            blockers = dict.fromkeys(service.KINDS, error.detail)
        if blockers["cv_claim_review"] is None:
            try:
                await cv_bundle(db, parent)
            except HTTPException as error:
                blockers["cv_claim_review"] = error.detail
        rows = list(await db.scalars(select(AssessmentRun).where(AssessmentRun.parent_run_id == parent.id,
            AssessmentRun.recruiter_id == recruiter_id, AssessmentRun.kind.in_(service.KINDS))
            .order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc()).limit(100)))
        items = []
        for row in rows:
            saved = await db.get(AssessmentResult, row.id)
            payload = saved.payload if saved and row.status == "completed" else None
            if payload and payload.get("artifact_sha256") != analysis.digest({k: v for k, v in payload.items() if k != "artifact_sha256"}):
                raise HTTPException(409, "Post-interview result integrity check failed.")
            reviews = list(await db.scalars(select(AssessmentFindingReview).where(AssessmentFindingReview.run_id == row.id)
                .order_by(AssessmentFindingReview.created_at, AssessmentFindingReview.id)))
            items.append({"run": public_run(row), "result": payload, "reviews": [public_review(r) for r in reviews]})
        return {"parent_run_id": str(parent.id), "blockers": blockers, "items": items,
                "scoring_settings": await scoring_settings.snapshot(db, parent.job_id), "review_required": not parent.synthetic}


async def start(parent_id, recruiter_id, request):
    async with SessionFactory() as db:
        parent = await owned_run(db, parent_id, recruiter_id)
        await lock_job(db, parent.job_id)
        if not parent.synthetic and not request.confirm_f5_review:
            raise HTTPException(409, "Review saved F5 questions, scores and evidence before starting post-interview analysis.")
        base = await bundle(db, parent)
        settings = get_settings()
        options = asdict(ModelExecution(settings.post_interview_model_provider, settings.post_interview_model_name,
            settings.post_interview_model_context_tokens, settings.post_interview_model_output_tokens, settings.post_interview_model_timeout_seconds))
        created, blockers = [], {}
        for kind in dict.fromkeys(request.features):
            inputs = {**base, "prompt_version": service.PROMPT_VERSION, "rubric_version": service.VERSIONS[kind],
                "model_options": options, "f5_reviewer_id": str(recruiter_id)}
            if kind == "cv_claim_review":
                try:
                    inputs["cv"] = await cv_bundle(db, parent)
                except HTTPException as error:
                    blockers[kind] = error.detail
                    continue
            if kind == "interview_performance":
                inputs["scoring_settings"] = await scoring_settings.snapshot(db, parent.job_id)
            key = analysis.digest({"kind": kind, "job_id": str(parent.job_id), **inputs})
            inputs["source_key"] = key
            run = await db.scalar(select(AssessmentRun).where(AssessmentRun.recruiter_id == recruiter_id,
                                                             AssessmentRun.input_key == key))
            if run is None:
                run = AssessmentRun(recruiter_id=recruiter_id, job_id=parent.job_id,
                    application_id=parent.application_id, transcript_id=parent.transcript_id,
                    parent_run_id=parent.id, cv_assessment_id=parent.cv_assessment_id if kind == "cv_claim_review" else None,
                    kind=kind, synthetic=parent.synthetic, label=parent.label, input_key=key, inputs=inputs, total_groups=1)
                db.add(run)
                await db.flush()
            created.append(public_run(run))
        await db.commit()
        return {"runs": created, "blockers": blockers}


async def review(run_id, finding_id, recruiter_id, request):
    async with SessionFactory() as db:
        run = await owned_run(db, run_id, recruiter_id)
        saved = await db.get(AssessmentResult, run.id)
        if (run.kind != "cv_claim_review" or run.status != "completed" or not saved
                or saved.payload.get("artifact_sha256") != analysis.digest({k: v for k, v in saved.payload.items() if k != "artifact_sha256"})
                or finding_id not in {item["id"] for item in saved.payload.get("claims", [])}):
            raise HTTPException(404, "Completed CV claim finding not found.")
        if request.action == "clarification" and not request.note.strip():
            raise HTTPException(422, "Add a clarification before saving.")
        row = AssessmentFindingReview(run_id=run.id, recruiter_id=recruiter_id, finding_id=finding_id,
            payload={"action": request.action, "note": request.note.strip(), "artifact_sha256": saved.payload["artifact_sha256"]})
        db.add(row)
        await db.flush()
        result = public_review(row)
        await db.commit()
        return result


async def validate_parent(db, claim):
    parent = await db.get(AssessmentRun, claim["parent_run_id"])
    if (not parent or parent.recruiter_id != claim["recruiter_id"] or parent.job_id != claim["job_id"]
            or parent.application_id != claim["application_id"]):
        return False
    try:
        current = await bundle(db, parent)
        if any(current[key] != claim["inputs"][key] for key in current):
            return False
        if claim["kind"] == "cv_claim_review" and await cv_bundle(db, parent) != claim["inputs"]["cv"]:
            return False
    except HTTPException:
        return False
    # Keep this run's pinned rubric even if the job's current policy changed.
    return True
