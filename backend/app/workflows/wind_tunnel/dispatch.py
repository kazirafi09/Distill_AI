"""Queue-to-graph adapter; all graph invocation stays in workflows."""
from sqlalchemy import select

from app.models.user import User
from app.models.wind_tunnel import WindTunnelRun, WindTunnelTask
from app.services.jd.edit_policy import document_edit_version
from app.services.recruitment_access import lock_job, owned_job
from app.services.wind_tunnel import storage
from app.workflows.wind_tunnel.runtime import analysis_versions, run_wind_tunnel


async def execute(task_id, claim, *, session_factory, saver=False, root=None, stop=None):
    claim_token, stop_token = storage.worker_claim.set(claim), storage.worker_stop.set(stop)
    try:
        return await _execute(task_id, claim, session_factory=session_factory, saver=saver, root=root)
    finally:
        storage.worker_claim.reset(claim_token)
        storage.worker_stop.reset(stop_token)


async def _execute(task_id, claim, *, session_factory, saver, root):
    # Resolve provider metadata outside SQL transactions, including on Retry.
    versions = await analysis_versions()
    async with session_factory() as db:
        pending = await db.get(WindTunnelTask, task_id)
        await lock_job(db, pending.job_id)
        task = await db.scalar(select(WindTunnelTask).where(WindTunnelTask.id == task_id).with_for_update())
        if task.status != "running" or task.claim != claim:
            return
        user = await db.get(User, task.owner_id)
        if not user or not user.is_active or user.role != "recruiter":
            raise ValueError("DIAGNOSTIC_OWNER_UNAVAILABLE")
        job = await owned_job(db, task.job_id, task.owner_id)
        if document_edit_version(job) != task.source_version:
            task.status, task.error_code = "stale", "SUGGESTION_SOURCE_STALE"
            await db.commit()
            return
        if task.run_id:
            run = await db.scalar(select(WindTunnelRun).where(WindTunnelRun.id == task.run_id).with_for_update())
            if run.versions != versions:
                raise ValueError("DIAGNOSTIC_CONFIG_CHANGED_REQUEST_MORE")
        else:
            run = await storage.create_run(db, job.id, task.owner_id, versions,
                trigger=task.trigger, request_id=task.id if task.trigger == "more" else None)
            task.run_id = run.id
        run.resources = {**run.resources, "dispatch": {"task_id": str(task.id), "claim": claim}}
        run_id, owner_id = run.id, task.owner_id
        await db.commit()
    result = await run_wind_tunnel(run_id, owner_id, session_factory=session_factory, saver=saver, root=root)
    async with session_factory() as db:
        task = await db.scalar(select(WindTunnelTask).where(WindTunnelTask.id == task_id).with_for_update())
        if task.status == "running" and task.claim == claim:
            task.status = {"completed": "completed", "completed_with_issues": "limited"}.get(result["status"], "failed")
            task.error_code = result.get("error_code")
            task.lease_until = None
            await db.commit()
