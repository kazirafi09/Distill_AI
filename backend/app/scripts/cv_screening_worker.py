"""PostgreSQL-backed worker. Running rows/checkpoints recover after restart."""
import asyncio
import logging
from uuid import UUID
from sqlalchemy import select, text
from app.db.session import SessionFactory, engine
from app.models.cv_screening import ScreeningRun
from app.models.user import User
from app.services.cv.screening import WORKER_LOCK, digest, inputs_for, owned_job
from app.workflows.cv_screening import build_screening_graph
from app.workflows.runtime import open_async_workflow_checkpointer

logger = logging.getLogger("distill_ai.cv_worker")


async def run_once():
    async with engine.connect() as connection:
        locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
        await connection.commit()
        if not locked:
            return False
        run_id = None
        try:
            async with SessionFactory() as db:
                run = await db.scalar(select(ScreeningRun).where(ScreeningRun.status.in_(["queued", "running"]))
                    .order_by(ScreeningRun.created_at).limit(1).with_for_update(skip_locked=True))
                if not run:
                    return False
                run_id, attempt = run.id, run.attempt
                user = await db.get(User, run.recruiter_id)
                if not user or not user.is_active or user.role != "recruiter":
                    run.status, run.error_code = "failed", "RECRUITER_ACCESS_REVOKED"
                    await db.commit()
                    return True
                job = await owned_job(db, run.job_id, run.recruiter_id)
                if digest(await inputs_for(db, job, run.recruiter_id)) != run.input_key:
                    run.status = "outdated"
                    await db.commit()
                    return True
                run.status = "running"
                await db.commit()
            async with open_async_workflow_checkpointer() as saver:
                graph = build_screening_graph(saver)
                config = {"configurable": {"thread_id": f"cv-screen:{run_id}:{attempt}"}, "recursion_limit": 10000}
                snapshot = await graph.aget_state(config)
                await graph.ainvoke(None if snapshot.next else {"run_id": str(run_id)}, config)
            return True
        except Exception:
            # Log a safe code, never CV/model content or authorization headers.
            logger.warning("Screening run failed; saved work can be resumed")
            if run_id:
                async with SessionFactory() as db:
                    run = await db.get(ScreeningRun, run_id)
                    if run.status != "outdated":
                        run.status, run.error_code = "failed", "SCREENING_WORKER_FAILED"
                        await db.commit()
            return True
        finally:
            await connection.rollback()
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
            await connection.commit()


async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        worked = await run_once()
        if not worked:
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
