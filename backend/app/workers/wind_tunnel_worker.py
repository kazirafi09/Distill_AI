"""Single durable F11 dispatcher with leases, recovery and cancellation."""
import asyncio
import logging
from app.core.logging import setup_logging
from contextlib import suppress

from sqlalchemy import select, text

from app.db.session import SessionFactory, engine
from app.models.wind_tunnel import WindTunnelTask
from app.services.wind_tunnel import queue
from app.workflows.runtime import open_async_workflow_checkpointer
from app.workflows.wind_tunnel.dispatch import execute

logger = logging.getLogger(__name__)
WORKER_LOCK = 611290803


async def process(task_id, claim, *, session_factory=SessionFactory, saver=False, root=None):
    stop = asyncio.Event()
    async def keep_alive():
        while True:
            await asyncio.sleep(10)
            try:
                async with session_factory() as db:
                    alive = await queue.heartbeat(db, task_id, claim)
                    await db.commit()
            except Exception:
                # Losing the lease connection must stop further dispatch.
                alive = False
            if not alive:
                # Cooperatively stop at the next owned operation. Let in-flight
                # thread-backed file/vector writes finish under their locks.
                stop.set()
                return

    work = asyncio.create_task(execute(task_id, claim, session_factory=session_factory, saver=saver, root=root, stop=stop))
    monitor = asyncio.create_task(keep_alive())
    try:
        await work
    except asyncio.CancelledError:
        # Cancellation is durable; stale graph callbacks also fail their SQL fence.
        if not work.cancelled():
            work.cancel()
            with suppress(asyncio.CancelledError):
                await work
        if asyncio.current_task().cancelling():
            raise
    except Exception as exc:
        logger.warning("Wind Tunnel batch failed; saved work can be retried")
        async with session_factory() as db:
            task = await db.scalar(select(WindTunnelTask).where(WindTunnelTask.id == task_id).with_for_update())
            if task and task.status == "running" and task.claim == claim:
                code = str(exc) if str(exc) in {"DIAGNOSTIC_CONFIG_CHANGED_REQUEST_MORE", "DIAGNOSTIC_OWNER_UNAVAILABLE"} else "DIAGNOSTIC_WORKER_FAILED"
                task.status, task.error_code, task.lease_until = "failed", code, None
                await db.commit()
    finally:
        monitor.cancel()
        with suppress(asyncio.CancelledError):
            await monitor


async def run_once():
    # Match the existing worker's reserved-connection advisory lock. Connection
    # death releases it; the next process reclaims expired SQL leases.
    async with engine.connect() as connection:
        locked = await connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": WORKER_LOCK})
        await connection.commit()
        if not locked:
            return False
        try:
            async with SessionFactory() as db:
                task = await queue.claim_next(db)
                await db.commit()
                if not task:
                    return False
                task_id, claim = task.id, task.claim
            async with open_async_workflow_checkpointer() as saver:
                await process(task_id, claim, saver=saver)
            return True
        finally:
            await connection.rollback()
            await connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": WORKER_LOCK})
            await connection.commit()


async def main():
    setup_logging("wind-tunnel-worker")
    while True:
        try:
            worked = await run_once()
        except Exception:
            logger.warning("Wind Tunnel dispatcher unavailable; retrying")
            worked = False
        if not worked:
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(main())
