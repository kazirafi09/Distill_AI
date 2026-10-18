"""Short, fenced PostgreSQL writes. No database transaction spans a model request."""
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select, func, or_, and_, update
from sqlalchemy.dialects.postgresql import insert

from app.db.session import SessionFactory
from app.models.assessments import AssessmentRun, AssessmentStage, AssessmentAnswer, AssessmentResult

LEASE_SECONDS = 60
TRANSIENT_ERRORS = {"ANALYSIS_MODEL_TIMEOUT", "ANALYSIS_MODEL_REQUEST_FAILED", "MODEL_RATE_LIMITED", "MODEL_NOT_AVAILABLE"}


class LeaseLost(RuntimeError):
    pass


async def leased(db, state):
    run = await db.scalar(select(AssessmentRun).where(
        AssessmentRun.id == UUID(state["storage_run_id"]),
        AssessmentRun.lease_token == UUID(state["storage_token"]),
        AssessmentRun.status == "running", AssessmentRun.lease_expires_at > func.now()).with_for_update())
    if run is None:
        raise LeaseLost("ASSESSMENT_LEASE_LOST")
    return run


async def claim(run_id=None):
    async with SessionFactory() as db, db.begin():
        statement = select(AssessmentRun).where(AssessmentRun.kind.in_(["f5", "f6"]), or_(
            and_(AssessmentRun.status == "queued", AssessmentRun.next_attempt_at <= func.now()),
            and_(AssessmentRun.status == "running", AssessmentRun.lease_expires_at <= func.now())))
        if run_id:
            statement = statement.where(AssessmentRun.id == run_id)
        run = await db.scalar(statement.order_by(AssessmentRun.created_at).limit(1).with_for_update(skip_locked=True))
        if run is None:
            return None
        if run.attempt >= 6:
            run.status, run.error_code = "failed", "ASSESSMENT_ATTEMPTS_EXHAUSTED"
            run.lease_token = run.lease_expires_at = None
            return None
        now = await db.scalar(select(func.now()))
        run.attempt += 1
        run.status, run.stage, run.error_code = "running", "loading", None
        run.lease_token, run.heartbeat_at = uuid4(), now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        return {"storage_run_id": str(run.id), "storage_token": str(run.lease_token),
            "inputs": run.inputs, "recruiter_id": run.recruiter_id, "job_id": run.job_id,
            "application_id": run.application_id, "transcript_id": run.transcript_id,
            "kind": run.kind, "parent_run_id": run.parent_run_id}


async def heartbeat(state):
    async with SessionFactory() as db, db.begin():
        result = await db.execute(update(AssessmentRun).where(
            AssessmentRun.id == UUID(state["storage_run_id"]),
            AssessmentRun.lease_token == UUID(state["storage_token"]),
            AssessmentRun.status == "running", AssessmentRun.lease_expires_at > func.now())
            .values(heartbeat_at=func.now(), lease_expires_at=func.now() + timedelta(seconds=LEASE_SECONDS)))
        return result.rowcount == 1


async def read_stage(state, name):
    async with SessionFactory() as db, db.begin():
        run = await leased(db, state)
        row = await db.get(AssessmentStage, (run.id, name))
        return row.payload if row else None


async def write_stage(state, name, payload):
    async with SessionFactory() as db, db.begin():
        run = await leased(db, state)
        await db.execute(insert(AssessmentStage).values(run_id=run.id, name=name, payload=payload)
            .on_conflict_do_update(index_elements=["run_id", "name"], set_={"payload": payload, "updated_at": func.now()}))
        run.stage = name.removesuffix("-attempts")
        if name == "grouping":
            run.total_groups = len(payload["groups"])
        if run.kind == "f6" and name in {"f6-strengths", "f6-weaknesses", "f6-evidence_gaps", "f6-recommended_areas"}:
            run.total_groups = 4
            run.completed_groups = await db.scalar(select(func.count()).select_from(AssessmentStage).where(
                AssessmentStage.run_id == run.id, AssessmentStage.name.in_([
                    "f6-strengths", "f6-weaknesses", "f6-evidence_gaps", "f6-recommended_areas"])))


async def save_answer(state, result):
    async with SessionFactory() as db, db.begin():
        run = await leased(db, state)
        # Immutable group rows make replay safe even if the process died after this commit.
        await db.execute(insert(AssessmentAnswer).values(run_id=run.id, group_id=result["group"]["id"],
            ordinal=state["group_index"], payload=result).on_conflict_do_nothing())
        run.completed_groups = await db.scalar(select(func.count()).select_from(AssessmentAnswer).where(AssessmentAnswer.run_id == run.id))


async def finish(state, artifact):
    async with SessionFactory() as db, db.begin():
        run = await leased(db, state)
        await db.execute(insert(AssessmentResult).values(run_id=run.id, payload=artifact)
            .on_conflict_do_update(index_elements=["run_id"], set_={"payload": artifact}))
        error = artifact.get("error_code")
        if artifact["status"] in {"completed", "completed_synthetic"}:
            run.status, run.stage = "completed", "completed"
        elif artifact["status"] == "failed" and error in TRANSIENT_ERRORS and run.attempt < 3:
            run.status, run.stage = "queued", "retry_wait"
            run.next_attempt_at = await db.scalar(select(func.now() + timedelta(seconds=10 * 2 ** run.attempt)))
        else:
            run.status = "failed" if artifact["status"] == "failed" else "needs_review"
            run.stage = run.status
        run.error_code = error
        run.lease_token = run.lease_expires_at = None


async def fail(state, code):
    await finish(state, {"status": "failed", "error_code": code, "synthetic": state["inputs"]["prepared"]["synthetic"],
        "publishable": False, "f6_ready": False, "results": [], "groups": [], "coverage": []})
