"""Attach the authored, clearly synthetic long interview to Sadia's local test entry.

Creates no analysis runs and makes no model calls. Refuses to replace an existing session.
"""
import argparse
import asyncio
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from uuid import UUID, uuid4

from sqlalchemy import select
from app.db.session import SessionFactory, engine
from app.models.cv import Candidate, CandidateApplication
from app.models.user import User  # Register the session's owner foreign key for standalone execution.
from app.models.cv_screening import CVAssessment
from app.models.interviews import InterviewSession, InterviewShortlistMember
from app.models.interview_transcripts import InterviewTranscript, InterviewTranscriptTurn
from app.services.interviews.assessment_runs import real_prepared, execution_options
from app.services.interviews import analysis
from app.services.interviews.consolidation_runs import real_cv
from app.services.interviews.transcript_preparation import AlignedTurn
from app.services.recruitment_access import owned_job, lock_job

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "docs/fixtures/interview-analysis/sadia-rahman-long/transcript.txt"
MODEL = "sadia-rahman-long-v1"


async def seed(application_id):
    lines = [line.split("|", 1) for line in FIXTURE.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]
    if len(lines) < 80 or any(role not in {"INTERVIEWER", "CANDIDATE"} or not text.strip() for role, text in lines):
        raise ValueError("The authored long dialogue is invalid.")
    async with SessionFactory() as db:
        application = await db.get(CandidateApplication, application_id)
        candidate = await db.get(Candidate, application.candidate_id) if application else None
        if not candidate or candidate.full_name != "Sadia Rahman":
            raise ValueError("This fixture is authorized only for the named Sadia Rahman application.")
        await lock_job(db, application.job_description_id)
        job = await owned_job(db, application.job_description_id, candidate.recruiter_id)
        if job.title != "Junior Laravel Developer":
            raise ValueError("This fixture requires the Junior Laravel Developer job.")
        existing = await db.scalar(select(InterviewSession).where(InterviewSession.application_id == application.id))
        if existing:
            transcript = await db.scalar(select(InterviewTranscript).where(InterviewTranscript.session_id == existing.id,
                InterviewTranscript.provider == "synthetic_fixture", InterviewTranscript.model == MODEL))
            if not transcript:
                raise ValueError("An existing interview will not be replaced by synthetic dialogue.")
            rows = list(await db.scalars(select(InterviewTranscriptTurn).where(InterviewTranscriptTurn.transcript_id == transcript.id)))
            prepared = real_prepared(transcript, rows, existing)
        else:
            created = datetime.now(timezone.utc)
            session = InterviewSession(application_id=application.id, job_description_id=job.id,
                recruiter_id=candidate.recruiter_id, room_name=f"synthetic-test-{uuid4()}", status="completed")
            db.add(session)
            await db.flush()
            transcript = InterviewTranscript(session_id=session.id, provider="synthetic_fixture", model=MODEL, language="en",
                version=1, status="completed", quality_flags=["SYNTHETIC_DEVELOPMENT_ONLY"], finalized_at=created)
            db.add(transcript)
            await db.flush()
            streams = {role: uuid4() for role in ("recruiter", "candidate")}
            turns, cursor = [], 0
            for role, text in lines:
                speaker = "recruiter" if role == "INTERVIEWER" else "candidate"
                # Authored test timing only: approximate conversational pace plus pauses.
                duration = max(3000, round(len(text.split()) * 60000 / 145))
                identity = f"recruiter-{candidate.recruiter_id}" if speaker == "recruiter" else f"candidate-{application.id}"
                data = dict(id=uuid4(), transcript_id=transcript.id, stream_id=streams[speaker],
                    source_event_key=f"{streams[speaker]}:{cursor}:{cursor + duration}", speaker_role=speaker,
                    participant_identity=identity, text=text, stream_start_ms=cursor, stream_end_ms=cursor + duration,
                    session_start_ms=cursor, session_end_ms=cursor + duration,
                    quality_flags=["SYNTHETIC_DEVELOPMENT_ONLY"], created_at=created)
                turns.append(AlignedTurn.model_validate({**data, "timing_basis": "synthetic_authoring_ground_truth"}).model_dump(mode="json"))
                db.add(InterviewTranscriptTurn(**data))
                cursor += duration + (4500 if speaker == "candidate" else 2000)
            transcript.content_sha256 = analysis.digest(turns)
            await db.flush()
            rows = list(await db.scalars(select(InterviewTranscriptTurn).where(InterviewTranscriptTurn.transcript_id == transcript.id)))
            prepared = real_prepared(transcript, rows, session)
        criteria = [{key: c[key] for key in ("id", "text", "dimension", "priority")} for c in job.screening_profile["criteria"]]
        model_state = {"storage_run_id": str(uuid4()), "prepared": prepared, "criteria": criteria, "references": [],
            "model_options": execution_options(), "model_version": {"model": "preflight-only"}, "allow_synthetic": True}
        analysis.prepare_input(model_state)
        payload = {"turns": [[f"t{i + 1:03}", t["speaker_role"], t["text"], t["session_start_ms"], t["session_end_ms"]] for i, t in enumerate(prepared["turns"])]}
        size = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode())
        if size + 10000 > model_state["model_options"]["context_tokens"] - model_state["model_options"]["output_tokens"]:
            raise ValueError("The long fixture exceeds the conservative F5 input budget.")
        cv = await db.scalar(select(CVAssessment).join(InterviewShortlistMember,
            InterviewShortlistMember.assessment_id == CVAssessment.id).where(InterviewShortlistMember.application_id == application.id,
            CVAssessment.application_id == application.id, CVAssessment.status == "completed"))
        if not cv:
            raise ValueError("A pinned CV screening result is required to test F6 later.")
        cv_snapshot = {"id": str(cv.id), "run_id": str(cv.run_id), "score": cv.score, "matches": cv.matches,
            "file_version_ids": cv.file_version_ids, "parse_result_ids": cv.parse_result_ids,
            "index_ids": cv.index_ids, "processing_key": cv.processing_key}
        await real_cv(db, SimpleNamespace(synthetic=True, application_id=None, recruiter_id=candidate.recruiter_id,
            job_id=job.id, cv_assessment_id=cv.id, inputs={"test_application_id": str(application.id), "cv_snapshot": cv_snapshot,
                "profile_version": job.profile_version, "profile_snapshot": job.screening_profile, "criteria": criteria}))
        await db.commit()
        return {"name": candidate.full_name, "application_id": str(application.id), "job_id": str(job.id), "job_number": job.number,
            "transcript_id": str(transcript.id), "synthetic": True, "segments": len(prepared["turns"]),
            "words": sum(len(t["text"].split()) for t in prepared["turns"]), "duration_minutes": round(max(t["session_end_ms"] for t in prepared["turns"]) / 60000, 1),
            "grouping_payload_bytes": size, "cv_assessment_id": str(cv.id), "analysis_started": False}


async def main(application_id):
    try:
        result = await seed(application_id)
        output = ROOT / "storage/diagnostics/sadia-rahman-long-test.json"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps(result))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("application_id", type=UUID)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(parser.parse_args().application_id))
