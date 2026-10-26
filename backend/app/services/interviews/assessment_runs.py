"""Owned assessment entry points, status and immutable input snapshots."""
from dataclasses import asdict
from copy import deepcopy
from pathlib import Path
from uuid import UUID
import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, model_validator
from typing import Literal
from sqlalchemy import select, func, cast, String, case

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.integrations.cv_model import ModelExecution
from app.models.assessments import AssessmentRun, AssessmentAnswer, AssessmentResult, AssessmentStage
from app.models.cv import Candidate, CandidateApplication
from app.models.cv_screening import CVAssessment
from app.models.interviews import InterviewSession, InterviewShortlist, InterviewShortlistMember
from app.models.interview_transcripts import InterviewTranscript, InterviewTranscriptTurn
from app.services.recruitment_access import owned_job, owned_application, lock_job
from app.services.interviews.analysis import PROMPT_VERSION, digest
from app.services.interviews.rubric import RUBRIC_VERSION, dimension_summaries
from app.services.interviews.transcript_preparation import AlignedTurn, load_fixture, saved_text_content, ordered_turns

FIXTURE = Path(__file__).resolve().parents[4] / "docs/fixtures/interview-analysis/jd-5"
MAX_ATTEMPTS = 6
BLOCKING_FLAGS = {"FINALIZATION_PENDING", "SESSION_TIMELINE_NOT_ALIGNED", "MICROPHONE_TRACK_INTERRUPTED",
                  "AUDIO_GAP", "TRANSCRIPT_INCOMPLETE", "TIMING_UNCERTAIN"}


def synthetic_demo_available():
    return all((FIXTURE / name).is_file() for name in (
        "transcript.json", "timeline-ground-truth.json", "accuracy-references.json", "cv-screening.json"))


class StartAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["interview", "synthetic_demo"] = "interview"
    application_id: UUID | None = None

    @model_validator(mode="after")
    def valid_source(self):
        if (self.source == "interview") != (self.application_id is not None):
            raise ValueError("Choose an application for a real interview; synthetic samples cannot be attached to candidates.")
        return self


def execution_options():
    settings = get_settings()
    return asdict(ModelExecution(settings.f5_model_provider, settings.f5_model_name,
        settings.f5_model_context_tokens, settings.f5_model_output_tokens, settings.f5_model_timeout_seconds))


def transcript_blocker(transcript):
    if transcript is None:
        return "No saved transcript yet."
    if transcript.status != "completed" or not transcript.finalized_at or not transcript.content_sha256:
        if transcript.status == "finalizing":
            return "Finalizing the transcript. Analysis becomes available when timing and saved content are validated."
        code = getattr(transcript, "error_code", None)
        if code == "STREAM_TIMING_MISSING":
            return "Audio timing was not recorded for this transcript. Saved text is available, but a new interview is needed for timed analysis."
        if code == "CAPTURE_PROCESS_LOST":
            return "Transcription was interrupted by a process restart. Saved text is retained; audio timing cannot be verified."
        if code in {"TRANSCRIPT_CAPTURE_NEEDS_REVIEW", "STREAM_TIMING_OR_FLUSH_UNVERIFIED"}:
            return "Audio capture contains an interruption or unverified timing. Review the saved transcript; analysis remains blocked."
        return "Transcript timing and finalization are not ready."
    if "NO_USABLE_TRANSCRIPT_TEXT" in transcript.quality_flags:
        return "The interview ended without usable saved speech. No Q&A can be analyzed."
    if "NO_USABLE_QA_TEXT" in transcript.quality_flags:
        return "The transcript is final, but speech from both interviewer and candidate is needed for Q&A analysis."
    if "TEXT_ONLY_FINALIZED" not in transcript.quality_flags and BLOCKING_FLAGS.intersection(transcript.quality_flags):
        return "Transcript has unresolved capture or timing gaps."
    return None


def real_prepared(transcript, rows, session):
    """Accept only measured, finalized session times; never guess offsets from save order."""
    blocker = transcript_blocker(transcript)
    if blocker:
        raise HTTPException(409, blocker)
    if not 1 <= len(rows) <= 1000:
        raise HTTPException(409, "Transcript must contain 1–1000 aligned segments.")
    synthetic = transcript.provider == "synthetic_fixture"
    if synthetic and "SYNTHETIC_DEVELOPMENT_ONLY" not in transcript.quality_flags:
        raise HTTPException(409, "Synthetic transcript is missing its development label.")
    text_only = "TEXT_ONLY_FINALIZED" in transcript.quality_flags and not synthetic
    turns = []
    if text_only:
        try:
            turns = saved_text_content(transcript, rows, session)
        except ValueError:
            raise HTTPException(409, "Saved transcript source identity or content is invalid.") from None
    for row in ([] if text_only else rows):
        if BLOCKING_FLAGS.intersection(row.quality_flags):
            raise HTTPException(409, "Transcript segment has unresolved capture or timing gaps.")
        identity = f"recruiter-{session.recruiter_id}" if row.speaker_role == "recruiter" else f"candidate-{session.application_id}"
        if row.participant_identity != identity:
            raise HTTPException(409, "Transcript speaker identity does not match this interview.")
        data = {name: getattr(row, name) for name in (
            "id", "transcript_id", "stream_id", "source_event_key", "speaker_role", "participant_identity", "text",
            "stream_start_ms", "stream_end_ms", "session_start_ms", "session_end_ms", "quality_flags", "created_at")}
        try:
            turns.append(AlignedTurn.model_validate({**data, "timing_basis": "synthetic_authoring_ground_truth" if synthetic else "finalized_session_timeline"}).model_dump(mode="json"))
        except ValueError:
            raise HTTPException(409, "Transcript has incomplete or invalid session timing.") from None
    turns = ordered_turns(turns)
    if {t["speaker_role"] for t in turns} != {"recruiter", "candidate"}:
        raise HTTPException(409, "Both speakers are required for interview analysis.")
    if digest(turns) != transcript.content_sha256:
        raise HTTPException(409, "Finalized transcript digest does not match its saved segments.")
    return {"synthetic": synthetic, "status": "synthetic_preview" if synthetic else "finalized", "f5_ready": True,
        "source_transcript_id": str(transcript.id), "source_version": transcript.version,
        "source_status": transcript.status, "source_quality_flags": transcript.quality_flags,
        "finalized_at": transcript.finalized_at.isoformat(), "fixture_id": transcript.model if synthetic else None,
        "fixture_revision": transcript.version if synthetic else None,
        "source_snapshot_sha256": digest({"transcript": str(transcript.id), "version": transcript.version,
            "finalized_at": transcript.finalized_at.isoformat(), "content": transcript.content_sha256}),
        "aligned_content_sha256": transcript.content_sha256, "turns": turns,
        **({"text_only": True, "timing_warning": "Saved text is final. Common timestamps and cross-speaker chronology are unverified; audio gaps may omit speech."} if text_only else {})}


async def latest_transcript(db, job_id, application_id, recruiter_id):
    return await db.scalar(select(InterviewTranscript).join(InterviewSession).where(
        InterviewSession.job_description_id == job_id, InterviewSession.application_id == application_id,
        InterviewSession.recruiter_id == recruiter_id).order_by(InterviewTranscript.created_at.desc(),
        InterviewTranscript.version.desc()).limit(1))


async def owned_run(db, run_id, recruiter_id, *, lock=False):
    statement = select(AssessmentRun).where(AssessmentRun.id == run_id, AssessmentRun.recruiter_id == recruiter_id)
    if lock:
        statement = statement.with_for_update()
    run = await db.scalar(statement)
    if run is None:
        raise HTTPException(404, "Assessment run not found.")
    await owned_job(db, run.job_id, recruiter_id)
    return run


def public_run(run):
    from app.services.interviews.consolidation import PROMPT_VERSION as F6_PROMPT_VERSION
    current_prompt = F6_PROMPT_VERSION if run.kind == "f6" else PROMPT_VERSION
    current_rubric = None
    if run.kind in {"cv_claim_review", "interview_performance", "interview_quality"}:
        from app.services.interviews.post_interview_analysis import PROMPT_VERSION as POST_INTERVIEW_PROMPT
        from app.services.interviews.post_interview_analysis import VERSIONS as POST_INTERVIEW_VERSIONS
        current_prompt = POST_INTERVIEW_PROMPT
        current_rubric = POST_INTERVIEW_VERSIONS[run.kind]
    return {"id": str(run.id), "job_id": str(run.job_id),
        "parent_run_id": str(run.parent_run_id) if run.parent_run_id else None,
        "application_id": str(run.application_id) if run.application_id else None,
        "kind": run.kind, "synthetic": run.synthetic, "label": run.label, "status": run.status,
        "can_recover": can_recover(run), "recovered_from": (run.inputs.get("recovery") or {}).get("source_run_id"),
        "stage": run.stage, "attempt": run.attempt, "max_attempts": MAX_ATTEMPTS,
        "completed_groups": run.completed_groups, "total_groups": run.total_groups,
        "error_code": run.error_code, "can_retry": run.status == "failed" and run.attempt < MAX_ATTEMPTS
            and run.inputs.get("prompt_version") == current_prompt
            and (current_rubric is None or run.inputs.get("rubric_version") == current_rubric)
            and run.error_code not in {"ASSESSMENT_SOURCE_CHANGED", "F6_SOURCE_CHANGED", "POST_INTERVIEW_SOURCE_CHANGED", "RECRUITER_ACCESS_REVOKED"},
        "profile_version": run.inputs.get("profile_version"), "prompt_version": run.inputs.get("prompt_version"),
        "rubric_version": run.inputs.get("rubric_version"),
        "heartbeat_at": run.heartbeat_at.isoformat() if run.heartbeat_at else None,
        "created_at": run.created_at.isoformat(), "updated_at": run.updated_at.isoformat(),
        "next_attempt_at": run.next_attempt_at.isoformat()}


def can_recover(run):
    return (run.kind == "f5" and run.status == "needs_review" and run.error_code == "ANALYSIS_OUTPUT_NEEDS_REVIEW"
        and not run.inputs.get("recovery") and run.inputs.get("prompt_version") in {"interview-analysis-9", "interview-analysis-11", "interview-analysis-12", "interview-analysis-13", PROMPT_VERSION}
        and run.inputs.get("rubric_version") == RUBRIC_VERSION)


def recovery_policy(name, payload):
    """Only known repairable failures can receive a single recovery budget."""
    code = payload.get("last_error")
    if name == "mapping-attempts" and code in {"MAPPING_CRITERION_INVALID", "MAPPING_COVERAGE_INVALID"}:
        return "mapping-id-recovery-1"
    if not name.startswith("evaluation-") or not name.endswith("-attempts"):
        return None
    if code == "EVIDENCE_QUOTE_INVALID":
        return "evidence-selection-recovery-1"
    errors = payload.get("last_details", {}).get("validation_errors", [])
    if code == "SCHEMA_INVALID" and errors:
        if all(e.get("code") == "SCORE_REASON_CONFLICT" for e in errors):
            return "score-reason-recovery-1"
        if all(e.get("field") == ["evidence"] and e.get("type") == "too_long" for e in errors):
            return "evidence-selection-recovery-1"
    return None


async def recover_run(run_id, recruiter_id):
    """One auditable child run, preserving validated stages and the original repair history."""
    async with SessionFactory() as db:
        source = await owned_run(db, run_id, recruiter_id)
        await lock_job(db, source.job_id)
        if not can_recover(source):
            raise HTTPException(409, "This run is not eligible for bounded schema recovery.")
        existing = await db.scalar(select(AssessmentRun).where(
            AssessmentRun.parent_run_id == source.id, AssessmentRun.recruiter_id == recruiter_id,
            AssessmentRun.kind == "f5",
            AssessmentRun.inputs["recovery"]["source_run_id"].astext == str(source.id)))
        if existing:
            return public_run(existing)
        stages = list(await db.scalars(select(AssessmentStage).where(AssessmentStage.run_id == source.id)))
        failed = sorted([s for s in stages if s.name.endswith("-attempts")
                         and s.payload.get("count", 0) >= 2
                         and not any(t.name == s.name.removesuffix("-attempts") for t in stages)], key=lambda s: s.name)
        policies = [recovery_policy(s.name, s.payload) for s in failed]
        if (not failed or None in policies or len(set(policies)) != 1
                or (len(failed) > 1 and (policies[0] != "evidence-selection-recovery-1"
                    or len({s.name.split("-")[1] for s in failed}) != 1))):
            raise HTTPException(409, "Recovery requires an exhausted supported validation failure with no accepted output.")
        recovery = {"policy": policies[0], "source_run_id": str(source.id),
            "inherited_prompt_version": source.inputs["prompt_version"], "target_prompt_version": PROMPT_VERSION,
            "retry_stage": failed[0].name.removesuffix("-attempts"),
            "retry_stages": [s.name.removesuffix("-attempts") for s in failed],
            "inherited_completed_groups": source.completed_groups,
            "source_attempts_sha256": digest({s.name: s.payload for s in failed})}
        inputs = {**deepcopy(source.inputs), "prompt_version": PROMPT_VERSION, "recovery": recovery}
        key = digest(inputs)
        child = await db.scalar(select(AssessmentRun).where(AssessmentRun.recruiter_id == recruiter_id, AssessmentRun.input_key == key))
        if not child:
            child = AssessmentRun(recruiter_id=source.recruiter_id, job_id=source.job_id, application_id=source.application_id,
                transcript_id=source.transcript_id, cv_assessment_id=source.cv_assessment_id, parent_run_id=source.id,
                kind="f5", synthetic=source.synthetic, label=source.label, inputs=inputs, input_key=key, total_groups=source.total_groups)
            db.add(child)
            await db.flush()
            for stage in stages:
                if stage.name not in {s.name for s in failed}:
                    db.add(AssessmentStage(run_id=child.id, name=stage.name, payload=deepcopy(stage.payload)))
            db.add(AssessmentStage(run_id=child.id, name="recovery-source-diagnostics",
                payload=deepcopy(failed[0].payload if len(failed) == 1 else {s.name: s.payload for s in failed})))
        await db.commit()
        return public_run(child)


async def start_run(job_id, recruiter_id, request):
    async with SessionFactory() as db:
        await lock_job(db, job_id)
        job = await owned_job(db, job_id, recruiter_id)
        criteria = [{key: criterion[key] for key in ("id", "text", "dimension", "priority")}
            for criterion in (job.screening_profile or {}).get("criteria", [])]
        if not criteria or not job.profile_version:
            raise HTTPException(409, "Save the job's screening criteria before starting an assessment.")
        transcript = None
        cv = None
        if request.source == "synthetic_demo":
            if not synthetic_demo_available():
                raise HTTPException(409, "Synthetic sample data is not installed on this computer. Select an interview instead.")
            prepared = load_fixture(FIXTURE / "transcript.json", FIXTURE / "timeline-ground-truth.json")
            references = json.loads((FIXTURE / "accuracy-references.json").read_text(encoding="utf-8"))["references"]
            label = "Synthetic Laravel sample"
        else:
            application = await owned_application(db, job_id, request.application_id, recruiter_id)
            candidate = await db.get(Candidate, application.candidate_id)
            label = candidate.full_name
            transcript = await latest_transcript(db, job_id, application.id, recruiter_id)
            blocker = transcript_blocker(transcript)
            if blocker:
                raise HTTPException(409, blocker)
            session = await db.get(InterviewSession, transcript.session_id)
            if session.status != "completed":
                raise HTTPException(409, "Interview must be completed before analysis.")
            rows = list(await db.scalars(select(InterviewTranscriptTurn).where(
                InterviewTranscriptTurn.transcript_id == transcript.id).limit(1001)))
            prepared = real_prepared(transcript, rows, session)
            if prepared["synthetic"]:
                label += " — synthetic test"
            references = []
            # Preserve the CV result actually used for interview selection, never substitute a newer run.
            cv = await db.scalar(select(CVAssessment).join(InterviewShortlistMember,
                InterviewShortlistMember.assessment_id == CVAssessment.id).join(InterviewShortlist,
                InterviewShortlist.id == InterviewShortlistMember.shortlist_id).where(
                InterviewShortlist.job_description_id == job_id, InterviewShortlist.recruiter_id == recruiter_id,
                InterviewShortlistMember.application_id == application.id, CVAssessment.application_id == application.id,
                CVAssessment.status == "completed"))
        inputs = {"prepared": prepared, "criteria": criteria, "references": references,
            "profile_version": job.profile_version, "profile_snapshot": job.screening_profile, "prompt_version": PROMPT_VERSION,
            "rubric_version": RUBRIC_VERSION, "model_options": execution_options(),
            "job_id": str(job_id), "application_id": str(request.application_id) if request.application_id else None,
            "cv_assessment_id": str(cv.id) if cv else None}
        if request.source != "synthetic_demo":
            from app.services.interviews.accuracy_references import snapshot_sources
            inputs["reference_sources"] = await snapshot_sources(db, recruiter_id)
        if cv:
            inputs["cv_snapshot"] = {"id": str(cv.id), "run_id": str(cv.run_id), "score": cv.score,
                "matches": cv.matches, "file_version_ids": cv.file_version_ids, "parse_result_ids": cv.parse_result_ids,
                "index_ids": cv.index_ids, "processing_key": cv.processing_key}
        if request.application_id and prepared["synthetic"]:
            # Retain an owned test-source link while keeping synthetic results out of candidate outcome rows.
            inputs["test_application_id"] = str(request.application_id)
        key = digest(inputs)
        run = await db.scalar(select(AssessmentRun).where(AssessmentRun.recruiter_id == recruiter_id, AssessmentRun.input_key == key))
        if not run:
            run = AssessmentRun(recruiter_id=recruiter_id, job_id=job_id,
                application_id=None if prepared["synthetic"] else request.application_id,
                transcript_id=transcript.id if transcript and not prepared["synthetic"] else None, cv_assessment_id=cv.id if cv else None,
                kind="f5", synthetic=prepared["synthetic"], label=label, input_key=key, inputs=inputs)
            db.add(run)
            await db.flush()
        await db.commit()
        return public_run(run)


async def retry_run(run_id, recruiter_id):
    async with SessionFactory() as db:
        run = await owned_run(db, run_id, recruiter_id, lock=True)
        if run.status != "failed" or run.attempt >= MAX_ATTEMPTS:
            raise HTTPException(409, "Only failed runs with remaining attempts can be retried.")
        if not public_run(run)["can_retry"]:
            raise HTTPException(409, "Processing version changed. Start a new assessment.")
        run.status, run.error_code, run.stage = "queued", None, "queued"
        run.next_attempt_at = await db.scalar(select(func.now()))
        run.lease_token = run.lease_expires_at = None
        await db.commit()
        return public_run(run)


async def run_status(run_id, recruiter_id):
    async with SessionFactory() as db:
        return public_run(await owned_run(db, run_id, recruiter_id))


async def run_result(run_id, recruiter_id):
    async with SessionFactory() as db:
        run = await owned_run(db, run_id, recruiter_id)
        result = await db.get(AssessmentResult, run.id)
        result_payload = dict(result.payload) if result else None
        if result_payload is not None and run.kind == "f6" and run.status == "needs_review":
            # Older withheld results predate validation_error; keep their reason visible too.
            validation = await db.get(AssessmentStage, (run.id, "f6-validation"))
            if validation:
                result_payload["validation_error"] = validation.payload.get("error")
        answers = list(await db.scalars(select(AssessmentAnswer).where(AssessmentAnswer.run_id == (run.parent_run_id if run.kind == "f6" else run.id))
            .order_by(AssessmentAnswer.ordinal)))
        payloads = [answer.payload for answer in answers]
        job = await owned_job(db, run.job_id, recruiter_id)
        f6 = None
        if run.kind == "f5":
            from app.services.interviews.consolidation_runs import eligibility
            f6 = await eligibility(db, run)
        from app.services.interviews.final_score import for_run
        # Project the immutable selected CV, never substitute a newer screening run.
        cv = run.inputs.get("cv") if run.kind == "f6" else run.inputs.get("cv_snapshot")
        pinned_cv = ({key: deepcopy(cv[key]) for key in
            ("id", "run_id", "score", "matches", "file_version_ids", "parse_result_ids") if key in cv}
            if cv else None)
        base_id = run.parent_run_id if run.kind == "f6" else run.id
        consolidated = run if run.kind == "f6" else await db.scalar(select(AssessmentRun).where(
            AssessmentRun.parent_run_id == base_id, AssessmentRun.kind == "f6",
            AssessmentRun.recruiter_id == recruiter_id, AssessmentRun.job_id == run.job_id,
            AssessmentRun.synthetic == run.synthetic, AssessmentRun.application_id == run.application_id,
        ).order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc()).limit(1))
        prepared = run.inputs["prepared"]
        return {"run": public_run(run), "result": result_payload, "f6": f6,
            "pinned_cv": pinned_cv, "consolidation": public_run(consolidated) if consolidated else None,
            "source_metadata": {"synthetic": run.synthetic, "transcript_version": prepared.get("source_version"),
                "text_only": prepared.get("text_only", False), "quality_flags": prepared.get("source_quality_flags", []),
                "timing_basis": "synthetic_authoring_ground_truth" if run.synthetic else
                    "saved_text_order" if prepared.get("text_only") else "finalized_session_timeline"},
            "final_score": await for_run(db, run, result_payload),
            "answers": payloads, "dimension_summaries": dimension_summaries(payloads),
            "criteria": run.inputs["criteria"], "source_turns": run.inputs["prepared"]["turns"],
            "outdated": job.profile_version != run.inputs.get("profile_version") or digest(
                job.screening_profile) != digest(run.inputs.get("profile_snapshot"))}


async def workspace(job_id, recruiter_id, offset=0):
    page_size = 5
    async with SessionFactory() as db:
        job = await owned_job(db, job_id, recruiter_id)
        rows = (await db.execute(select(CandidateApplication, Candidate).join(Candidate).where(
            CandidateApplication.job_description_id == job_id, Candidate.recruiter_id == recruiter_id)
            .order_by(Candidate.full_name, CandidateApplication.id).offset(offset).limit(page_size + 1))).all()
        application_ids = [application.id for application, _ in rows[:page_size]]
        # Load each candidate's latest stages independently of the 50-run history window.
        # Synthetic interview runs deliberately have no real candidate outcome FK.
        # Their owned test-source link is retained in inputs for display and reuse.
        candidate_key = func.coalesce(cast(AssessmentRun.application_id, String), case(
            (AssessmentRun.synthetic.is_(True), AssessmentRun.inputs["test_application_id"].astext)))
        ranked_runs = select(AssessmentRun.id, func.row_number().over(
            partition_by=(candidate_key, AssessmentRun.kind),
            order_by=(AssessmentRun.created_at.desc(), AssessmentRun.id.desc())).label("ordinal")).where(
            AssessmentRun.job_id == job_id, AssessmentRun.recruiter_id == recruiter_id,
            candidate_key.in_([str(application_id) for application_id in application_ids])).subquery()
        latest_runs = list(await db.scalars(select(AssessmentRun).where(AssessmentRun.id.in_(
            select(ranked_runs.c.id).where(ranked_runs.c.ordinal == 1)))))
        by_candidate = {(str(run.application_id) if run.application_id else
            run.inputs.get("test_application_id") if run.synthetic else None, run.kind): run for run in latest_runs}
        sessions = list(await db.scalars(select(InterviewSession).where(
            InterviewSession.job_description_id == job_id, InterviewSession.recruiter_id == recruiter_id,
            InterviewSession.application_id.in_(application_ids))))
        by_session = {session.application_id: session for session in sessions}
        candidates = []
        for application, candidate in rows[:page_size]:
            transcript = await latest_transcript(db, job_id, application.id, recruiter_id)
            synthetic = transcript is not None and transcript.provider == "synthetic_fixture"
            session = by_session.get(application.id)
            f5 = by_candidate.get((str(application.id), "f5"))
            f6 = by_candidate.get((str(application.id), "f6"))
            def stage(run):
                if run is None:
                    return None
                outdated = job.profile_version != run.inputs.get("profile_version") or digest(
                    job.screening_profile) != digest(run.inputs.get("profile_snapshot"))
                return {**public_run(run), "outdated": outdated}
            candidates.append({"id": str(application.id), "name": candidate.full_name + (" — synthetic test" if synthetic else ""),
                "email": candidate.email, "synthetic": synthetic,
                "interview_status": session.status if session else None,
                "interviewed_at": session.completed_at.isoformat() if session and session.completed_at else None,
                "transcript_status": transcript.status if transcript else None,
                "transcript_version": transcript.version if transcript else None,
                "analysis": stage(f5), "assessment": stage(f6),
                "assessment_blocker": "The CV screening result pinned to this interview analysis is missing." if (
                    f5 and f5.status == "completed" and not f6 and not f5.synthetic and not f5.cv_assessment_id) else None,
                "blocker": transcript_blocker(transcript), "ready": transcript_blocker(transcript) is None})
        runs = list(await db.scalars(select(AssessmentRun).where(AssessmentRun.job_id == job_id,
            AssessmentRun.recruiter_id == recruiter_id, AssessmentRun.kind.in_(["f5", "f6"])).order_by(AssessmentRun.created_at.desc(), AssessmentRun.id.desc()).limit(51)))
        return {"candidates": candidates, "synthetic_demo_available": synthetic_demo_available(),
            "has_more_candidates": len(rows) > page_size, "offset": offset, "page_size": page_size,
            "runs": [public_run(run) for run in runs[:50]], "has_more_runs": len(runs) > 50,
            "profile_version": job.profile_version,
            "profile_ready": bool((job.screening_profile or {}).get("criteria") and job.profile_version)}
