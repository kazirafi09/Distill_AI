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
from app.assistant import router as assistant_router

from starlette.concurrency import run_in_threadpool

from app.workflows.runtime import open_workflow_graphs
from app.jd import router as jd_router
from app.jd_reference_routes import router as jd_references_router
from app.cv_routes import router as cv_router
from app.cv_parsing_routes import router as cv_parsing_router
from app.cv_screening_routes import router as cv_screening_router
from app.mcp_cv import mcp as cv_mcp, transport_app as cv_mcp_app

setup_logging()
logger = logging.getLogger("distill_ai.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    resources = ExitStack()

    try:
        jd_graph, assistant_graph = await run_in_threadpool(
            resources.enter_context,
            open_workflow_graphs(),
        )

        app.state.jd_graph = jd_graph
        app.state.assistant_graph = assistant_graph

        logger.info(
            "Application started; PostgreSQL JD and assistant graphs ready"
        )
        async with cv_mcp.session_manager.run():
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
app.include_router(jd_references_router)
app.include_router(assistant_router)
app.include_router(cv_router)
app.include_router(cv_parsing_router)
app.include_router(cv_screening_router)
app.mount("/mcp", cv_mcp_app)

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
