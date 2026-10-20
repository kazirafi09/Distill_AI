from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field

from app.auth import DatabaseSession, bearer, require_recruiter
from app.core.security import decode_access_identity
from app.models.user import User
from app.services.assistant.streaming import conversation_stream
from app.services.assistant.conversations import (
    assistant_response, create_assistant_conversation, get_owned_assistant,
    open_saved_jd, send_assistant_jd_action, send_assistant_message,
    list_assistant_conversations,
)

router = APIRouter(prefix="/assistant", tags=["Assistant"])
Recruiter = Annotated[User, Depends(require_recruiter)]


class AssistantMessage(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    message: str = Field(min_length=1, max_length=6000)
    checkpoint_id: str | None = Field(...)
    interview_session_id: UUID | None = None


class AssistantJDAction(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    checkpoint_id: str | None = Field(...)
    jd_checkpoint_id: str = Field(min_length=1)
    interrupt_id: str = Field(min_length=1)
    action: Literal["reply", "revise", "approve", "cancel"]
    message: str | None = Field(default=None, min_length=1, max_length=6000)


@router.post("/conversations", status_code=201)
async def create_conversation(request: Request, user: Recruiter, db: DatabaseSession):
    conversation = await create_assistant_conversation(db, user.id)
    return await assistant_response(
        db, request.app.state.assistant_graph, request.app.state.jd_graph, conversation,
    )


@router.get("/conversations")
async def list_conversations(
    request: Request, user: Recruiter, db: DatabaseSession,
    limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0),
):
    return await list_assistant_conversations(db, request.app.state.assistant_graph, user.id, limit=limit, offset=offset)


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID, request: Request, user: Recruiter, db: DatabaseSession,
):
    conversation = await get_owned_assistant(db, user.id, conversation_id)
    return await assistant_response(
        db, request.app.state.assistant_graph, request.app.state.jd_graph, conversation,
    )


@router.post("/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: UUID, body: AssistantMessage, request: Request,
    user: Recruiter, db: DatabaseSession,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)],
):
    async def send():
        return await send_assistant_message(
            db, request.app.state.assistant_graph, request.app.state.jd_graph,
            user.id, conversation_id, body.message, body.checkpoint_id,
            session_id=decode_access_identity(credentials.credentials)[1],
            interview_session_id=body.interview_session_id,
        )

    if "text/event-stream" in request.headers.get("accept", ""):
        return conversation_stream(send)
    return await send()


@router.post("/conversations/{conversation_id}/jd-actions")
async def send_jd_action(
    conversation_id: UUID, body: AssistantJDAction, request: Request,
    user: Recruiter, db: DatabaseSession,
):
    async def send():
        return await send_assistant_jd_action(
            db, request.app.state.assistant_graph, request.app.state.jd_graph,
            user.id, conversation_id, **body.model_dump(),
        )

    if "text/event-stream" in request.headers.get("accept", ""):
        return conversation_stream(send)
    return await send()


@router.post("/job-descriptions/{description_id}/conversation")
async def open_description(
    description_id: UUID, request: Request, user: Recruiter, db: DatabaseSession,
):
    return await open_saved_jd(
        db, request.app.state.assistant_graph, request.app.state.jd_graph,
        user.id, description_id,
    )
