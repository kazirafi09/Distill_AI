"""Short owned queue operations. No model calls or graph imports."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select

from app.models.wind_tunnel import WindTunnelTask
from app.services.jd.edit_policy import document_edit_version, require_editable
from app.services.recruitment_access import lock_job, owned_job
from app.services.wind_tunnel.storage import ANALYSIS_VERSION, digest

LEASE_SECONDS = 90


def now():
    return datetime.now(timezone.utc)


async def enqueue(db, job, owner_id, *, request_id=None):
    # Approval and the outbox commit together. Invalid criteria/model downtime
    # are worker failures and cannot undo a valid job approval.
    await lock_job(db, job.id)
    version = document_edit_version(job)
    trigger = "more" if request_id else "approval"
    key = digest([trigger, str(request_id)] if request_id else [trigger, version, ANALYSIS_VERSION])
    existing = await db.scalar(select(WindTunnelTask).where(
        WindTunnelTask.job_id == job.id, WindTunnelTask.request_key == key))
    if existing:
        if existing.source_version != version:
            raise HTTPException(409, "This request belongs to an earlier job version.")
        return existing
    if request_id:
        await require_editable(db, job)
        active = await db.scalar(select(WindTunnelTask.id).where(WindTunnelTask.job_id == job.id,
            WindTunnelTask.status.in_(["queued", "running"])).limit(1))
        if active:
            raise HTTPException(409, "Suggestions are already being generated for this job.")
    task = WindTunnelTask(owner_id=owner_id, job_id=job.id, source_version=version,
        request_key=key, trigger=trigger, status="queued", attempts=0)
    db.add(task)
    await db.flush()
    return task


async def owned_task(db, job_id, task_id, owner_id, *, lock=False):
    await owned_job(db, job_id, owner_id)
    query = select(WindTunnelTask).where(WindTunnelTask.id == task_id,
        WindTunnelTask.job_id == job_id, WindTunnelTask.owner_id == owner_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    task = await db.scalar(query)
    if task is None:
        raise HTTPException(404, "Suggestion batch not found.")
    return task


async def retry(db, job_id, task_id, owner_id):
    # Job advisory lock serializes More/retry. Editor locks are acquired only
    # by the editor; workers never hold this lock during a provider request.
    await lock_job(db, job_id)
    task = await owned_task(db, job_id, task_id, owner_id, lock=True)
    job = await owned_job(db, job_id, owner_id)
    await require_editable(db, job)
    if task.source_version != document_edit_version(job):
        raise HTTPException(409, "This batch is stale. Request More suggestions for the current job.")
    if task.status == "queued":
        return task
    if task.status not in {"failed", "limited", "cancelled"}:
        raise HTTPException(409, "This batch cannot be retried.")
    active = await db.scalar(select(WindTunnelTask.id).where(WindTunnelTask.job_id == job_id,
        WindTunnelTask.id != task.id, WindTunnelTask.status.in_(["queued", "running"])).limit(1))
    if active:
        raise HTTPException(409, "Another batch is active for this job.")
    task.status, task.claim, task.lease_until, task.error_code = "queued", None, None, None
    return task


async def cancel(db, job_id, task_id, owner_id):
    task = await owned_task(db, job_id, task_id, owner_id, lock=True)
    if task.status in {"queued", "running"}:
        task.status, task.claim, task.lease_until = "cancelled", None, None
    return task


async def claim_next(db):
    task = await db.scalar(select(WindTunnelTask).where(or_(WindTunnelTask.status == "queued",
        (WindTunnelTask.status == "running") & (WindTunnelTask.lease_until < now())))
        .order_by(WindTunnelTask.created_at, WindTunnelTask.id).with_for_update(skip_locked=True).limit(1))
    if task:
        task.status, task.claim = "running", str(uuid4())
        task.lease_until, task.attempts = now() + timedelta(seconds=LEASE_SECONDS), task.attempts + 1
        task.error_code = None
    return task


async def heartbeat(db, task_id, claim):
    task = await db.scalar(select(WindTunnelTask).where(WindTunnelTask.id == task_id).with_for_update())
    if not task or task.status != "running" or task.claim != claim or task.lease_until <= now():
        return False
    task.lease_until = now() + timedelta(seconds=LEASE_SECONDS)
    return True
