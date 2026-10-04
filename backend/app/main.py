import logging
from contextlib import ExitStack, asynccontextmanager
from time import perf_counter
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import register_error_handlers
from app.core.logging import setup_logging
from app.db.session import engine, get_db
from app.auth import router as auth_router

from starlette.concurrency import run_in_threadpool

from app.workflows.jd.runtime import open_jd_graph
from app.jd import router as jd_router

setup_logging()
logger = logging.getLogger("distill_ai.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    resources = ExitStack()

    try:
        app.state.jd_graph = await run_in_threadpool(
            resources.enter_context,
            open_jd_graph(),
        )

        logger.info("Application started; PostgreSQL JD graph ready")
        yield

    finally:
        try:
            await run_in_threadpool(resources.close)
        finally:
            await engine.dispose()
            logger.info("Application stopped; database connections closed")


app = FastAPI(
    title="Distill AI",
    lifespan=lifespan,
)

register_error_handlers(app)
app.include_router(auth_router)
app.include_router(jd_router)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    started_at = perf_counter()
    status_code = 500

    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    finally:
        duration_ms = (perf_counter() - started_at) * 1000

        route = request.scope.get("route")
        route_path = getattr(route, "path", "<unmatched>")

        level = (
            logging.ERROR if status_code >= 500
            else logging.WARNING if status_code >= 400
            else logging.INFO
        )

        logger.log(
            level,
            "method=%s | route=%s | status=%s | duration_ms=%.2f",
            request.method,
            route_path,
            status_code,
            duration_ms,
        )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/health/db")
async def database_health(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    try:
        await db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        logger.warning("Database health check failed")

        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        ) from None

    return {"status": "ok", "database": "connected"}