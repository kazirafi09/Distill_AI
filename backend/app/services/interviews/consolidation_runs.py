"""Owned F5→F6 handoff with exact pinned CV evidence; no rescreening or retrieval."""
from dataclasses import asdict
import hashlib
import json
from uuid import UUID, NAMESPACE_URL, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.integrations.cv_model import ModelExecution
from app.models.assessments import AssessmentRun, AssessmentResult
from app.models.cv import CVFileVersion, CVDocument
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.services.recruitment_access import lock_job, owned_application
from app.services.cv.scoring import calculate_scores
from app.services.interviews import analysis, consolidation as service
from app.services.interviews.assessment_runs import FIXTURE, owned_run, public_run
from app.services.interviews.rubric import RUBRIC_VERSION


class StartConsolidation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm_f5_review: StrictBool = False


def criteria_key(criteria):
    return analysis.digest([{key: c[key] for key in ("id", "text", "dimension", "priority")} for c in criteria])


def synthetic_cv(parent):
    if not all((FIXTURE / name).is_file() for name in ("cv-screening.json", "transcript.json")):
        raise HTTPException(409, "Synthetic CV sample data is not installed on this computer.")
    fixture = json.loads((FIXTURE / "cv-screening.json").read_text(encoding="utf-8"))
    interview = json.loads((FIXTURE / "transcript.json").read_text(encoding="utf-8"))
    if (fixture["synthetic"] is not True or fixture["interview_fixture_id"] != parent.inputs["prepared"]["fixture_id"]
            or criteria_key(parent.inputs["criteria"]) != criteria_key(interview["provenance"]["criteria_snapshot"])):
        raise HTTPException(409, "This synthetic CV fixture is compatible only with the JD 5 Laravel sample criteria.")
    file_id = str(uuid5(NAMESPACE_URL, fixture["id"] + ":file"))
    parse_id = str(uuid5(NAMESPACE_URL, fixture["id"] + ":parse"))
    matches = []
    for match in fixture["matches"]:
        start = fixture["text"].index(match["quote"])
        matches.append({"requirement_id": match["requirement_id"], "decision": match["decision"],
            "justification": match["justification"], "unsupported_requirement_parts": [],
            "evidence": [{"file_version_id": file_id, "parse_id": parse_id, "source_id": "page-1",
                "start": start, "end": start + len(match["quote"]), "quote": match["quote"], "ocr_derived": False}]})
    profile = {"criteria": parent.inputs["criteria"], "dimension_weights": fixture["dimension_weights"]}
    return {"id": fixture["id"], "synthetic": True, "score": calculate_scores(profile, matches), "matches": matches,
        "provenance": fixture["provenance"], "documents": [{"parse_id": parse_id, "file_version_id": file_id,
            "file_sha256": hashlib.sha256(fixture["text"].encode()).hexdigest(), "text": fixture["text"],
            "sources": [{"id": "page-1", "start": 0, "end": len(fixture["text"]), "method": "synthetic"}]}]}


async def real_cv(db, parent):
    application_id = parent.application_id
    if parent.synthetic and parent.inputs.get("test_application_id"):
        application_id = UUID(parent.inputs["test_application_id"])
    await owned_application(db, parent.job_id, application_id, parent.recruiter_id)
    row = (await db.execute(select(CVAssessment, ScreeningRun).join(ScreeningRun, ScreeningRun.id == CVAssessment.run_id).where(
        CVAssessment.id == parent.cv_assessment_id, CVAssessment.application_id == application_id,
        CVAssessment.status == "completed", ScreeningRun.job_id == parent.job_id,
        ScreeningRun.recruiter_id == parent.recruiter_id))).one_or_none()
    if row is None:
        raise HTTPException(409, "The exact completed CV assessment pinned to F5 is unavailable. No newer result will be substituted.")
    cv, screening = row
    expected = parent.inputs.get("cv_snapshot")
    snapshot = {"id": str(cv.id), "run_id": str(cv.run_id), "score": cv.score, "matches": cv.matches,
        "file_version_ids": cv.file_version_ids, "parse_result_ids": cv.parse_result_ids,
        "index_ids": cv.index_ids, "processing_key": cv.processing_key}
    profile = screening.inputs.get("profile") or {}
    if (expected != snapshot or screening.inputs.get("profile_version") != parent.inputs["profile_version"]
            or criteria_key(profile.get("criteria", [])) != criteria_key(parent.inputs["criteria"])
            or analysis.digest(profile) != analysis.digest(parent.inputs["profile_snapshot"])):
        raise HTTPException(409, "CV and interview snapshots have incompatible criteria, versions or changed results.")
    documents = []
    for parse_id in cv.parse_result_ids:
        parsed = (await db.execute(select(CVParseResult, CVFileVersion).join(CVFileVersion,
            CVFileVersion.id == CVParseResult.file_version_id).join(CVDocument, CVDocument.id == CVFileVersion.cv_document_id).where(
            CVParseResult.id == UUID(parse_id), CVParseResult.status == "completed", CVDocument.application_id == application_id,
            CVDocument.job_description_id == parent.job_id))).one_or_none()
        if parsed is None:
            raise HTTPException(409, "Pinned CV source text is unavailable.")
        parse, file = parsed
        if str(file.id) not in cv.file_version_ids or parse.file_sha256 != file.file_sha256 or not parse.text:
            raise HTTPException(409, "Pinned CV file/parse integrity does not match.")
        documents.append({"parse_id": str(parse.id), "file_version_id": str(file.id), "file_sha256": file.file_sha256,
            "text": parse.text, "sources": parse.sources})
    return {"id": str(cv.id), "run_id": str(cv.run_id), "synthetic": False, "score": cv.score, "matches": cv.matches,
        "documents": documents, "screening_versions": {"profile_version": screening.inputs["profile_version"],
            "versions": screening.inputs.get("versions"), "rubric": screening.inputs.get("rubric")}}


async def load_bundle(db, parent):
    if parent.kind != "f5" or parent.status != "completed":
        raise HTTPException(409, "F6 requires a completed F5 result.")
    saved = await db.get(AssessmentResult, parent.id)
    f5 = saved.payload if saved else {}
    expected_status = "completed_synthetic" if parent.synthetic else "completed"
    if (f5.get("status") != expected_status or f5.get("synthetic") is not parent.synthetic
            or f5.get("rubric_version") != RUBRIC_VERSION
            or f5.get("artifact_sha256") != analysis.digest({k: v for k, v in f5.items() if k != "artifact_sha256"})):
        raise HTTPException(409, "F5 result integrity, version or readiness is invalid.")
    cv = synthetic_cv(parent) if parent.synthetic and not parent.inputs.get("test_application_id") else await real_cv(db, parent)
    bundle = {"parent_run_id": str(parent.id), "criteria": parent.inputs["criteria"],
        "profile_version": parent.inputs["profile_version"], "profile_snapshot": parent.inputs["profile_snapshot"],
        "prepared": parent.inputs["prepared"], "references": f5.get("references", parent.inputs["references"]), "f5": f5, "cv": cv,
        "f5_input_key": parent.input_key, "f5_prompt_version": parent.inputs["prompt_version"]}
    if parent.inputs.get("test_application_id"):
        bundle["test_application_id"] = parent.inputs["test_application_id"]
    try:
        service.build_sources(bundle)
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "Saved CV/F5 source evidence failed validation.") from None
    return bundle


async def eligibility(db, parent):
    try:
        await load_bundle(db, parent)
        return {"ready": True, "blocker": None, "review_required": not parent.synthetic}
    except HTTPException as error:
        return {"ready": False, "blocker": error.detail, "review_required": not parent.synthetic}


async def start(parent_id, recruiter_id, request):
    async with SessionFactory() as db:
        parent = await owned_run(db, parent_id, recruiter_id)
        await lock_job(db, parent.job_id)
        if not parent.synthetic and not request.confirm_f5_review:
            raise HTTPException(409, "Review F5 scores and evidence, then confirm that review before starting F6.")
        inputs = await load_bundle(db, parent)
        settings = get_settings()
        inputs.update(prompt_version=service.PROMPT_VERSION, rubric_version=service.REPORT_VERSION,
            model_options=asdict(ModelExecution(settings.f6_model_provider, settings.f6_model_name,
                settings.f6_model_context_tokens, settings.f6_model_output_tokens, settings.f6_model_timeout_seconds)),
            f5_review={"reviewer_id": str(recruiter_id), "artifact_sha256": inputs["f5"]["artifact_sha256"],
                "basis": "synthetic_development_only" if parent.synthetic else "explicit_reviewer_confirmation"})
        key = analysis.digest({"kind": "f6", "job_id": str(parent.job_id), **inputs})
        run = await db.scalar(select(AssessmentRun).where(AssessmentRun.recruiter_id == recruiter_id, AssessmentRun.input_key == key))
        if not run:
            run = AssessmentRun(recruiter_id=recruiter_id, job_id=parent.job_id, application_id=parent.application_id,
                transcript_id=parent.transcript_id, parent_run_id=parent.id, cv_assessment_id=parent.cv_assessment_id,
                kind="f6", synthetic=parent.synthetic, label=parent.label, input_key=key, inputs=inputs, total_groups=4)
            db.add(run)
            await db.flush()
        await db.commit()
        return public_run(run)
