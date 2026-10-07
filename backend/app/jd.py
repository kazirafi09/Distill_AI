from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.auth import DatabaseSession, require_recruiter
from app.models.jd import JDConversation, JobDescription
from app.models.user import User

from app.services.jd_conversations import (
    act_on_jd_conversation,
    create_jd_conversation,
    description_summary,
    load_jd_conversation,
)

router = APIRouter(prefix="/jd", tags=["Job Descriptions"])

Recruiter = Annotated[User, Depends(require_recruiter)]

class ConversationCreate(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    message: str = Field(min_length=1, max_length=6000)

class ConversationAction(BaseModel):
    model_config = ConfigDict(
        str_strip_whitespace=True,
        extra="forbid",
    )

    checkpoint_id: str = Field(min_length=1)
    interrupt_id: str = Field(min_length=1)
    action: Literal["reply", "revise", "approve", "cancel"]
    message: str | None = Field(
        default=None,
        min_length=1,
        max_length=6000,
    )


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: Recruiter,
    db: DatabaseSession,
):
    return await create_jd_conversation(
        db, request.app.state.jd_graph, user.id, body.message,
    )


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    request: Request,
    user: Recruiter,
    db: DatabaseSession,
):
    return await load_jd_conversation(
        db, request.app.state.jd_graph, user.id, conversation_id,
    )


@router.post("/conversations/{conversation_id}/actions")
async def act_on_conversation(
    conversation_id: UUID,
    body: ConversationAction,
    request: Request,
    user: Recruiter,
    db: DatabaseSession,
):
    return await act_on_jd_conversation(
        db, request.app.state.jd_graph, user.id, conversation_id,
        checkpoint_id=body.checkpoint_id,
        interrupt_id=body.interrupt_id,
        action=body.action,
        message=body.message,
    )


@router.get("/descriptions")
async def list_descriptions(
    user: Recruiter,
    db: DatabaseSession,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    result = await db.execute(
        select(
            JobDescription.id,
            JobDescription.conversation_id,
            JobDescription.title,
            JobDescription.created_at,
            JobDescription.number,
        )
        .join(
            JDConversation,
            JobDescription.conversation_id == JDConversation.id,
        )
        .where(JDConversation.recruiter_id == user.id)
        .order_by(
            JobDescription.created_at.desc(),
            JobDescription.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    return {
        "items": [
            {
                "id": str(row.id),
                "conversation_id": str(row.conversation_id),
                "title": row.title,
                "created_at": row.created_at,
                "number": row.number,
            }
            for row in result.all()
        ],
        "limit": limit,
        "offset": offset,
    }


@router.get("/descriptions/{description_id}")
async def get_description(
    description_id: UUID,
    user: Recruiter,
    db: DatabaseSession,
):
    document = await db.scalar(
        select(JobDescription)
        .join(
            JDConversation,
            JobDescription.conversation_id == JDConversation.id,
        )
        .where(
            JobDescription.id == description_id,
            JDConversation.recruiter_id == user.id,
        )
    )

    if document is None:
        raise HTTPException(
            status_code=404,
            detail="Job description not found",
        )

    return {
        **description_summary(document),
        "conversation_id": str(document.conversation_id),
        "content": document.content,
    }
