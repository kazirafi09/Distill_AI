"""Recruitment MCP tools mounted by the API; the assistant graph orchestrates chat."""

import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID

import jwt
from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from starlette.responses import JSONResponse

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.auth_session import AuthSession
from app.models.interviews import InterviewSession
from app.models.user import User
from app.services.assistant.conversations import assistant_config, get_owned_assistant
from app.services.interviews import delivery
from app.services.jd.discord_job_posts import post_job_to_discord as post_saved_job_to_discord
from app.services.question_banks.retrieval import retrieve_interview_questions
from app.services.jd.lookup import find_owned_jobs
from app.integrations.cv_results_mcp import read_screening_results
from app.workflows.assistant.state import AssistantState


class RecruitmentClaims(BaseModel):
    recruiter_id: UUID = Field(alias="sub")
    conversation_id: UUID
    allowed_tools: list[str] = Field(max_length=5)
    sid: UUID | None = None
    interview_session_id: UUID | None = None


@dataclass
class RecruitmentContext:
    recruiter_id: UUID
    allowed_tools: list[str]
    state: AssistantState
    interview_session_id: UUID | None = None
    conversation_id: UUID | None = None
    auth_session_id: UUID | None = None


authorized_context: ContextVar[RecruitmentContext] = ContextVar("recruitment_mcp_context")

mcp = FastMCP(
    "Distill AI Recruitment",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost:*", "127.0.0.1:*", "backend:*", "host.docker.internal:*"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
    ),
)


def get_authorized_context(tool_name: str) -> RecruitmentContext:
    context = authorized_context.get()
    if tool_name not in context.allowed_tools:
        raise ValueError("This credential does not permit that tool.")
    # Interview conversations cannot execute other recruitment tools.
    if (
        context.interview_session_id is not None
        and tool_name != "search_interview_questions"
    ):
        raise ValueError("Only question retrieval is allowed in interview chat.")
    return context


def saved_job(context: RecruitmentContext) -> UUID | None:
    identifier = context.state.get("job_description_id")
    return UUID(identifier) if identifier else None


def invitation_preview_was_shown(messages, review) -> bool:
    """Preserve the existing preview → assistant reply → later human reply check."""
    human_index = next(
        (i for i in range(len(messages) - 1, -1, -1) if isinstance(messages[i], HumanMessage)),
        None,
    )
    if human_index is None:
        return False
    for index in range(human_index - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, ToolMessage) or message.name != "preview_interview_invitations":
            continue
        try:
            preview = json.loads(message.content)
        except (ValueError, TypeError):
            continue
        if not isinstance(preview, dict) or preview.get("batch_id") != review.get("batch_id"):
            continue
        return any(
            isinstance(reply, AIMessage) and not reply.tool_calls
            and isinstance(reply.content, str) and reply.content.strip()
            for reply in messages[index + 1:human_index]
        )
    return False


@mcp.tool(structured_output=True)
async def find_jobs(job_number: int | None = None, title: str | None = None,
                    limit: int = 5, offset: int = 0) -> dict[str, Any]:
    """Read the recruiter's saved jobs and display their information directly in chat.

    Use the user's exact job number OR a literal title fragment. Omit both to list
    recent saved jobs. A unique number/title match includes the full saved job
    description and screening profile. Several matches return summaries: ask the
    recruiter to choose a job number rather than guessing. Use next_offset for more
    results. Never navigate, open a job conversation, or start a JD workflow.
    """
    context = get_authorized_context("find_jobs")
    return await find_owned_jobs(context.recruiter_id, job_number=job_number,
        title=title, limit=limit, offset=offset)


@mcp.tool(structured_output=True)
async def get_screening_results(job_number: int | None = None, limit: int = 5, offset: int = 0,
                                 view: Literal["ranked", "issues", "all"] = "ranked") -> dict[str, Any]:
    """Read saved screening results directly in chat; never start parsing or screening.

    Use the exact JD number or omit it for this chat's associated saved JD. Use
    ranked for the top candidates, issues for processing failures/review needs,
    or all for ranked and unranked candidates. Use next_offset to read more.
    Older/withheld scores are not current ranks. Scores are not hiring decisions.
    """
    context = get_authorized_context("get_screening_results")
    if not 1 <= limit <= 20 or not 0 <= offset <= 10000 or (job_number is not None and not 1 <= job_number < 2**63):
        return {"status": "invalid_request", "message": "Use a positive JD number, limit 1–20 and offset 0–10000."}
    try:
        job_id = await delivery.resolve_job(context.recruiter_id, job_number, saved_job(context))
        result = await read_screening_results(context.recruiter_id, context.conversation_id, job_id,
            session_id=context.auth_session_id, limit=limit, offset=offset, view=view)
    except HTTPException as error:
        return {"status": "failed", "message": error.detail, "screening_started": False}
    except Exception as error:
        logging.getLogger(__name__).warning("Saved screening lookup failed | error_type=%s", type(error).__name__)
        return {"status": "failed", "message": "Saved screening results could not be read. Please try again. No screening run was started.",
                "screening_started": False}
    return {**result, **({"job_number": job_number} if job_number is not None else {})}


@mcp.tool(structured_output=True)
async def start_jd_creation() -> dict[str, Any]:
    """Request a new JD workflow only when the user explicitly asks to create a JD."""
    context = get_authorized_context("start_jd_creation")
    if context.state.get("jd_conversation_id"):
        return {"status": "failed", "message": "Start a new conversation to create another JD."}
    return {
        "status": "requested",
        "jd_start_requested": True,
        "message": "JD creation requested. No JD has been generated or saved yet.",
    }


@mcp.tool(structured_output=True)
async def post_job_to_discord(job_number: int | None = None) -> dict[str, Any]:
    """Post an owned approved JD only after an explicit request to post to Discord.

    Use the user's exact job number, or omit it to use this chat's saved JD.
    Do not automatically retry unknown outcomes.
    """
    context = get_authorized_context("post_job_to_discord")
    if context.state.get("mode") == "jd_workflow":
        return {"status": "failed", "error_code": "JOB_APPROVAL_REQUIRED"}
    return await run_in_threadpool(
        post_saved_job_to_discord,
        context.recruiter_id,
        job_number=job_number,
        job_description_id=saved_job(context) if job_number is None else None,
    )


@mcp.tool(structured_output=True)
async def preview_interview_invitations(
    job_number: int | None = None,
    mode: Literal["new", "retry_failed", "reissue"] = "new",
) -> dict[str, Any]:
    """Prepare a preview; this does not send email.

    Show the job, sender, all recipients/actions, subject, body and link lifetime.
    Ask whether to send this exact preview. Only a later user reply can approve.
    Use retry_failed or reissue only on an explicit user request.
    """
    context = get_authorized_context("preview_interview_invitations")
    try:
        job_id = await delivery.resolve_job(context.recruiter_id, job_number, saved_job(context))
        result = await delivery.prepare_invitations(job_id, context.recruiter_id, mode)
    except HTTPException as exc:
        return {"status": "failed", "message": exc.detail}
    result["confirmation_prompt"] = "Would you like me to send these invitations?"
    return result


@mcp.tool(structured_output=True)
async def confirm_interview_invitations(approval_message: str) -> dict[str, Any]:
    """Queue the shown preview after an explicit later user approval.

    Copy approval_message verbatim from that reply. Questions, refusal,
    hesitation, conditions or requested changes are not approval. Prepare a
    fresh preview if the job, recipients or content change. Queueing is not delivery.
    """
    context = get_authorized_context("confirm_interview_invitations")
    review = context.state.get("invitation_review")
    messages = context.state.get("messages", [])
    latest = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    if (
        not review or latest is None or not isinstance(latest.content, str)
        or not latest.content.strip() or approval_message.strip() != latest.content.strip()
        or not invitation_preview_was_shown(messages, review)
    ):
        return {"status": "failed", "message": "Review the invitation preview, then confirm that you want to send it."}
    try:
        return await delivery.confirm_invitations(
            UUID(review["job_id"]), context.recruiter_id,
            UUID(review["batch_id"]), review["preview_key"],
        )
    except HTTPException as exc:
        return {"status": "failed", "message": exc.detail}


@mcp.tool(structured_output=True)
async def get_interview_invitation_status(job_number: int | None = None) -> dict[str, Any]:
    """Read owned invitation outcomes. Queued/provider_accepted are not confirmed delivery."""
    context = get_authorized_context("get_interview_invitation_status")
    try:
        job_id = await delivery.resolve_job(context.recruiter_id, job_number, saved_job(context))
        invitations = await delivery.invitation_status(job_id, context.recruiter_id)
    except HTTPException as exc:
        return {"status": "failed", "message": exc.detail}
    return {"job_id": str(job_id), "invitations": invitations}


@mcp.tool(structured_output=True)
async def search_interview_questions(limit: int = 6) -> dict[str, Any]:
    """Find questions for the interview associated with this conversation.

    Always call this tool before suggesting interview questions.
    The backend uses the saved JD title to search uploaded question banks.

    For retrieved results, suggest questions grounded in the returned chunks
    and cite their filenames and source locations.

    For no_relevant_matches or no_ready_banks, you may create questions
    yourself, clearly labeled AI-generated.

    For failed results, explain the retrieval problem. Do not interpret
    failure as evidence that no matching questions exist.
    """
    context = get_authorized_context("search_interview_questions")

    session_id = context.interview_session_id
    if not session_id:
        return {
            "status": "failed",
            "error_code": "INTERVIEW_CONTEXT_REQUIRED",
        }

    if not 1 <= limit <= 10:
        return {
            "status": "failed",
            "error_code": "QUESTION_LIMIT_INVALID",
        }

    try:
        async with SessionFactory() as db:
            session = await db.scalar(
                select(InterviewSession).where(
                    InterviewSession.id == session_id,
                    InterviewSession.recruiter_id == context.recruiter_id,
                )
            )
            if session is None:
                return {
                    "status": "failed",
                    "error_code": "INTERVIEW_NOT_FOUND",
                }

            job_id = session.job_description_id

        result = await retrieve_interview_questions(
            recruiter_id=context.recruiter_id,
            job_description_id=job_id,
            limit=limit,
        )

    except Exception as exc:
        logging.getLogger("distill_ai.question_banks").error(
            "Interview retrieval failed | error_type=%s",
            type(exc).__name__,
        )
        return {
            "status": "failed",
            "error_code": "QUESTION_RETRIEVAL_FAILED",
        }

    return {
        **result,
        "interview_session_id": str(session_id),
    }


class RecruitmentAuth:
    """Accept backend-issued, audience-specific credentials; load state server-side."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        authorization = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
        scheme, _, token = authorization.partition(" ")
        try:
            if scheme.lower() != "bearer" or not token:
                raise ValueError("Missing credential")
            payload = jwt.decode(
                token, get_settings().jwt_secret_key.get_secret_value(),
                algorithms=["HS256"], audience="recruitment-mcp",
                options={"require": ["sub", "aud", "iat", "exp", "conversation_id", "allowed_tools"]},
            )
            # Bound delegated credentials even if the issuing code is misconfigured.
            now = datetime.now(timezone.utc).timestamp()
            if not 0 < payload["exp"] - payload["iat"] <= 120 or now - payload["iat"] > 120:
                raise ValueError("Credential lifetime exceeds two minutes")
            claims = RecruitmentClaims.model_validate(payload)
            if claims.interview_session_id is not None and any(
                name != "search_interview_questions" for name in claims.allowed_tools
            ):
                raise ValueError("Only question retrieval is allowed in interview chat")
        except (jwt.InvalidTokenError, ValidationError, ValueError, TypeError):
            return await JSONResponse({"detail": "Invalid recruitment MCP credential."}, status_code=401)(scope, receive, send)

        try:
            async with SessionFactory() as db:
                user = await db.get(User, claims.recruiter_id)
                if not user or not user.is_active or user.role != "recruiter":
                    raise HTTPException(403, "Active recruiter access required.")
                if claims.sid is not None:
                    session = await db.get(AuthSession, claims.sid)
                    if not session or session.user_id != user.id or session.expires_at <= datetime.now(timezone.utc):
                        raise HTTPException(401, "Browser session is no longer active.")
                await get_owned_assistant(db, user.id, claims.conversation_id)
                if claims.interview_session_id is not None:
                    interview = await db.get(InterviewSession, claims.interview_session_id)
                    if interview is None or interview.recruiter_id != user.id:
                        raise HTTPException(404, "Interview session not found.")
        except HTTPException as exc:
            return await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)

        graph = getattr(scope["app"].state, "assistant_graph", None)
        if graph is None:
            return await JSONResponse({"detail": "Assistant graph is unavailable."}, status_code=503)(scope, receive, send)
        snapshot = await run_in_threadpool(
            graph.get_state, assistant_config(claims.conversation_id, claims.recruiter_id),
        )
        context_token = authorized_context.set(RecruitmentContext(
            recruiter_id=claims.recruiter_id,
            allowed_tools=claims.allowed_tools,
            state=snapshot.values,
            interview_session_id=claims.interview_session_id,
            conversation_id=claims.conversation_id,
            auth_session_id=claims.sid,
        ))
        try:
            await self.app(scope, receive, send)
        finally:
            authorized_context.reset(context_token)


transport_app = RecruitmentAuth(mcp.streamable_http_app())
