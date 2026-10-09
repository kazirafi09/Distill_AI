"""Local MCP transport using existing recruiter JWTs, not tool-supplied identity.

Standalone: python -m uvicorn app.mcp_cv:app --host 0.0.0.0 --port 8002
Also mounted at /mcp/ on the existing API. No workflow-to-MCP recursion.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID
import jwt
from pydantic import BaseModel, Field

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount

from app.auth import get_current_user
from app.core.config import get_settings
from app.core.security import ALGORITHM
from app.db.session import SessionFactory
from app.services.cv.parsing import get_cv_profile as retrieve, parse_cv as process
from app.services.cv.profile_schema import CVParsingResponse

recruiter_context: ContextVar[UUID] = ContextVar("cv_mcp_recruiter")
evidence_assessment_context: ContextVar[UUID | None] = ContextVar("cv_mcp_assessment", default=None)
mcp = FastMCP("Distill AI CV Parsing", stateless_http=True, json_response=True,
              streamable_http_path="/", transport_security=TransportSecuritySettings(
                  enable_dns_rebinding_protection=True,
                  allowed_hosts=["localhost:*", "127.0.0.1:*", "backend:*", "host.docker.internal:*"],
                  allowed_origins=["http://localhost:*", "http://127.0.0.1:*"]))


@mcp.tool(structured_output=True)
async def parse_cv(file_version_id: str) -> CVParsingResponse:
    """Parse an owned, exact downloaded CV version. Retry busy requests later."""
    require_general_tools()
    return CVParsingResponse.model_validate(await process(UUID(file_version_id), recruiter_context.get()))


@mcp.tool(structured_output=True)
async def get_cv_profile(file_version_id: str) -> CVParsingResponse:
    """Read saved parsing progress, profile, evidence and warnings for an owned CV."""
    require_general_tools()
    return CVParsingResponse.model_validate(await retrieve(UUID(file_version_id), recruiter_context.get()))


def require_general_tools():
    if evidence_assessment_context.get() is not None:
        raise ValueError("This credential permits scoped evidence retrieval only.")


class EvidenceResponse(BaseModel):
    job_id: str
    application_id: str
    assessment_id: str | None = None
    evidence: list[dict] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ScreeningToolResponse(BaseModel):
    result: dict


@mcp.tool(structured_output=True)
async def search_cv_evidence(job_id: str, application_id: str, query: str) -> EvidenceResponse:
    """Retrieve owned application evidence from exact assessed versions; distances are not scores."""
    from app.services.cv.indexing import search_evidence
    return EvidenceResponse.model_validate(await search_evidence(UUID(job_id), UUID(application_id), query,
        recruiter_context.get(), evidence_assessment_context.get()))


@mcp.tool(structured_output=True)
async def start_job_screening(job_id: str) -> ScreeningToolResponse:
    """Persist a selected-JD screening run; the durable worker processes it."""
    require_general_tools()
    from app.services.cv.screening import start_job_screening as start
    return ScreeningToolResponse(result=await start(UUID(job_id), recruiter_context.get()))


@mcp.tool(structured_output=True)
async def get_screening_results(job_id: str) -> ScreeningToolResponse:
    """Read current ranks, scores, evidence, incomplete results and outdated history."""
    require_general_tools()
    from app.services.cv.screening import get_screening_results as results
    return ScreeningToolResponse(result=await results(UUID(job_id), recruiter_context.get()))


class RecruiterAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = authorization.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise HTTPException(401, "Recruiter authentication required.")
            async with SessionFactory() as db:
                user = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
                if user.role != "recruiter":
                    raise HTTPException(403, "Recruiter access required.")
                recruiter_id = user.id
                claims = jwt.decode(token, get_settings().jwt_secret_key.get_secret_value(), algorithms=[ALGORITHM])
                assessment_id = UUID(claims["cv_evidence_assessment"]) if claims.get("cv_evidence_assessment") else None
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        except (jwt.InvalidTokenError, ValueError):
            return await JSONResponse({"detail": "Invalid recruiter credential."}, status_code=401)(scope, receive, send)
        context_token = recruiter_context.set(recruiter_id)
        assessment_token = evidence_assessment_context.set(assessment_id)
        try:
            await self.app(scope, receive, send)
        finally:
            recruiter_context.reset(context_token)
            evidence_assessment_context.reset(assessment_token)


transport_app = RecruiterAuth(mcp.streamable_http_app())


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[Mount("/mcp", app=transport_app)], lifespan=lifespan)
