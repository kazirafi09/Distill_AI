"""Local MCP transport using existing recruiter JWTs, not tool-supplied identity.

Standalone: python -m uvicorn app.mcp_cv:app --host 0.0.0.0 --port 8002
Also mounted at /mcp/ on the existing API. No workflow-to-MCP recursion.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar
from uuid import UUID
from datetime import datetime, timezone
from typing import Literal
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
from app.integrations.cv_results_mcp import ResultsClaims, RESULTS_AUDIENCE
from app.models.user import User
from app.models.auth_session import AuthSession

recruiter_context: ContextVar[UUID] = ContextVar("cv_mcp_recruiter")
evidence_assessment_context: ContextVar[UUID | None] = ContextVar("cv_mcp_assessment", default=None)
results_job_context: ContextVar[UUID | None] = ContextVar("cv_mcp_results_job", default=None)


class ScopedCVMCP(FastMCP):
    async def list_tools(self):
        tools = await super().list_tools()
        if results_job_context.get() is not None:
            return [tool for tool in tools if tool.name == "get_screening_results"]
        if evidence_assessment_context.get() is not None:
            return [tool for tool in tools if tool.name == "search_cv_evidence"]
        return tools


mcp = ScopedCVMCP("Distill AI CV Parsing", stateless_http=True, json_response=True,
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
    if results_job_context.get() is not None:
        raise ValueError("This credential permits reading screening results only.")
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
    if results_job_context.get() is not None:
        raise ValueError("This credential permits reading screening results only.")
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
async def get_screening_results(job_id: str, limit: int | None = None, offset: int = 0,
                                view: Literal["ranked", "issues", "all"] = "ranked") -> ScreeningToolResponse:
    """Read saved results. Supply limit (1–20) for a bounded ranked/issues/all summary.

    Omitting limit preserves the full-results contract for existing clients.
    Delegated chatbot credentials always receive a bounded summary.
    """
    scoped_job = results_job_context.get()
    if scoped_job is not None:
        if UUID(job_id) != scoped_job:
            raise ValueError("This credential does not permit results for another job.")
        if limit is None:
            limit = 5
    else:
        require_general_tools()
    if limit is not None and (not 1 <= limit <= 20 or not 0 <= offset <= 10000):
        raise ValueError("Invalid results pagination.")
    from app.services.cv.screening import get_screening_results as results
    result = await results(UUID(job_id), recruiter_context.get())
    if limit is not None:
        from app.services.cv.result_summary import summarize_results
        result = summarize_results(result, limit=limit, offset=offset, view=view)
    return ScreeningToolResponse(result=result)


class RecruiterAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers", []))
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, _, token = authorization.partition(" ")
        delegated_job = None
        try:
            if scheme.lower() != "bearer" or not token:
                raise HTTPException(401, "Recruiter authentication required.")
            async with SessionFactory() as db:
                claims = jwt.decode(token, get_settings().jwt_secret_key.get_secret_value(), algorithms=[ALGORITHM], options={"verify_aud": False})
                if claims.get("aud") == RESULTS_AUDIENCE:
                    claims = jwt.decode(token, get_settings().jwt_secret_key.get_secret_value(), algorithms=[ALGORITHM], audience=RESULTS_AUDIENCE,
                        options={"require": ["sub", "iat", "exp", "aud", "conversation_id", "job_id", "tool"]})
                    if claims["exp"] - claims["iat"] > 120:
                        raise ValueError("Delegated credential lifetime is too long.")
                    delegated = ResultsClaims.model_validate(claims)
                    user = await db.get(User, delegated.recruiter_id)
                    if not user or not user.is_active:
                        raise HTTPException(403, "Active recruiter access required.")
                    if delegated.sid is not None:
                        session = await db.get(AuthSession, delegated.sid)
                        if not session or session.user_id != user.id or session.expires_at <= datetime.now(timezone.utc):
                            raise HTTPException(401, "Browser session is no longer active.")
                    from app.services.assistant.conversations import get_owned_assistant
                    await get_owned_assistant(db, user.id, delegated.conversation_id)
                    delegated_job = delegated.job_id
                else:
                    user = await get_current_user(HTTPAuthorizationCredentials(scheme="Bearer", credentials=token), db)
                if user.role != "recruiter":
                    raise HTTPException(403, "Recruiter access required.")
                recruiter_id = user.id
                assessment_id = UUID(claims["cv_evidence_assessment"]) if delegated_job is None and claims.get("cv_evidence_assessment") else None
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code,
                                      headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
        except (jwt.InvalidTokenError, ValueError, TypeError):
            return await JSONResponse({"detail": "Invalid recruiter credential."}, status_code=401)(scope, receive, send)
        context_token = recruiter_context.set(recruiter_id)
        assessment_token = evidence_assessment_context.set(assessment_id)
        results_token = results_job_context.set(delegated_job)
        try:
            await self.app(scope, receive, send)
        finally:
            recruiter_context.reset(context_token)
            evidence_assessment_context.reset(assessment_token)
            results_job_context.reset(results_token)


transport_app = RecruiterAuth(mcp.streamable_http_app())


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(routes=[Mount("/mcp", app=transport_app)], lifespan=lifespan)
