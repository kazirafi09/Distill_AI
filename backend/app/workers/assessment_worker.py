"""Durable F5/F6 queue worker with leased ownership and database-backed stage replay."""
import argparse
import asyncio
import logging
import sys
from uuid import UUID

from sqlalchemy import select
from fastapi import HTTPException

from app.db.session import SessionFactory, engine
from app.integrations.cv_model import ModelExecution, model_version
from app.models.user import User
from app.models.assessments import AssessmentRun, AssessmentResult
from app.models.interviews import InterviewSession
from app.models.interview_transcripts import InterviewTranscript, InterviewTranscriptTurn
from app.services.recruitment_access import owned_job
from app.services.interviews import analysis, assessment_store as store
from app.services.interviews.assessment_runs import real_prepared
from app.services.interviews.rubric import RUBRIC_VERSION
from app.workflows.interview_analysis.graph import build_interview_analysis_graph

logger = logging.getLogger("distill_ai.assessment_worker")


async def process(claim):
    inputs = claim["inputs"]
    from app.services.interviews import consolidation
    from app.services.interviews import post_interview_analysis
    is_post_interview = claim.get("kind") in post_interview_analysis.KINDS
    if claim.get("kind") not in {"f5", "f6", *post_interview_analysis.KINDS}:
        await store.fail(claim, "ASSESSMENT_KIND_UNSUPPORTED")
        return
    is_f6 = claim.get("kind") == "f6"
    prompt = consolidation.PROMPT_VERSION if is_f6 else analysis.PROMPT_VERSION
    rubric = consolidation.REPORT_VERSION if is_f6 else RUBRIC_VERSION
    if is_post_interview:
        prompt, rubric = post_interview_analysis.PROMPT_VERSION, post_interview_analysis.VERSIONS[claim["kind"]]
    if inputs["prompt_version"] != prompt or inputs["rubric_version"] != rubric:
        await store.fail(claim, "ASSESSMENT_VERSION_CHANGED")
        return
    async with SessionFactory() as db:
        user = await db.get(User, claim["recruiter_id"])
        if not user or not user.is_active or user.role != "recruiter":
            await store.fail(claim, "RECRUITER_ACCESS_REVOKED")
            return
        await owned_job(db, claim["job_id"], claim["recruiter_id"])
        if is_post_interview:
            from app.services.interviews.post_interview_runs import validate_parent
            if not await validate_parent(db, claim):
                await store.fail(claim, "POST_INTERVIEW_SOURCE_CHANGED")
                return
        if is_f6:
            parent = await db.get(AssessmentRun, claim["parent_run_id"])
            saved = await db.get(AssessmentResult, claim["parent_run_id"])
            if (not parent or parent.status != "completed" or parent.recruiter_id != claim["recruiter_id"]
                    or parent.job_id != claim["job_id"] or parent.application_id != claim["application_id"]
                    or not saved or analysis.digest(saved.payload) != analysis.digest(inputs["f5"])):
                await store.fail(claim, "F6_SOURCE_CHANGED")
                return
            if not parent.synthetic or parent.inputs.get("test_application_id"):
                from app.services.interviews.consolidation_runs import real_cv
                try:
                    current_cv = await real_cv(db, parent)
                except HTTPException:
                    current_cv = None
                if current_cv != inputs["cv"]:
                    await store.fail(claim, "F6_SOURCE_CHANGED")
                    return
        if not inputs["prepared"]["synthetic"] or inputs.get("test_application_id"):
            transcript_id = claim["transcript_id"] or UUID(inputs["prepared"]["source_transcript_id"])
            transcript = await db.get(InterviewTranscript, transcript_id)
            if not transcript:
                await store.fail(claim, "ASSESSMENT_SOURCE_CHANGED")
                return
            session = await db.get(InterviewSession, transcript.session_id)
            expected_application = claim["application_id"] or UUID(inputs["test_application_id"])
            if (not session or session.recruiter_id != claim["recruiter_id"] or session.job_description_id != claim["job_id"]
                    or session.application_id != expected_application):
                await store.fail(claim, "ASSESSMENT_SOURCE_CHANGED")
                return
            rows = list(await db.scalars(select(InterviewTranscriptTurn).where(
                InterviewTranscriptTurn.transcript_id == transcript.id).limit(1001)))
            try:
                current = real_prepared(transcript, rows, session)
            except HTTPException:
                current = None
            if session.status != "completed" or current != inputs["prepared"]:
                await store.fail(claim, "ASSESSMENT_SOURCE_CHANGED")
                return
    version = await store.read_stage(claim, "model-version")
    if version is None:
        version = await model_version(execution=ModelExecution(**inputs["model_options"]))
        await store.write_stage(claim, "model-version", version)
    if is_post_interview:
        await post_interview_analysis.execute(claim, version)
        return
    if is_f6:
        from app.workflows.assessment_consolidation.graph import build_consolidation_graph
        await build_consolidation_graph().ainvoke({**claim, "model_version": version}, {"recursion_limit": 100})
        return
    state = {**claim, "prepared": inputs["prepared"], "criteria": inputs["criteria"],
        "references": inputs["references"], "model_options": inputs["model_options"], "model_version": version,
        "allow_synthetic": inputs["prepared"]["synthetic"], "group_limit": 0}
    if inputs.get("recovery"):
        state["recovery"] = inputs["recovery"]
    final = await build_interview_analysis_graph().ainvoke(state, {"recursion_limit": 10000})
    await store.finish(claim, final["artifact"])


async def keep_alive(claim):
    while True:
        await asyncio.sleep(10)
        if not await store.heartbeat(claim):
            raise store.LeaseLost("ASSESSMENT_LEASE_LOST")


async def run_once(run_id=None):
    claim = await store.claim(run_id)
    if claim is None:
        return False
    execution = asyncio.create_task(process(claim))
    heartbeat = asyncio.create_task(keep_alive(claim))
    try:
        done, _ = await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    except store.LeaseLost:
        logger.warning("Assessment lease lost; stale worker stopped")
    except asyncio.CancelledError:
        # Keep the running lease: a replacement process recovers it after expiry.
        raise
    except Exception as error:
        safe = str(error) if isinstance(error, (ValueError, analysis.AnalysisError)) else "ASSESSMENT_WORKER_FAILED"
        allowed = store.TRANSIENT_ERRORS | {"MODEL_AUTH_FAILED", "MODEL_API_KEY_MISSING", "MODEL_REQUEST_INVALID"}
        try:
            await store.fail(claim, safe if safe in allowed else "ASSESSMENT_WORKER_FAILED")
        except store.LeaseLost:
            pass
        logger.warning("Assessment execution failed; durable checkpoints retained")
    finally:
        for task in (execution, heartbeat):
            task.cancel()
        await asyncio.gather(execution, heartbeat, return_exceptions=True)
    return True


async def main(once=False, run_id=None):
    try:
        while True:
            try:
                from app.services.interviews.transcript_finalization import reconcile_finalization
                await reconcile_finalization()
                worked = await run_once(run_id)
            except Exception:
                logger.warning("Assessment queue unavailable; retrying")
                worked = False
            if once:
                return
            if not worked:
                await asyncio.sleep(3)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--run-id", type=UUID)
    args = parser.parse_args()
    from app.core.logging import setup_logging
    setup_logging("assessment-worker")
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main(args.once, args.run_id))
