from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from langchain_core.messages import HumanMessage
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.auth import DatabaseSession, require_recruiter
from app.models.jd import JDConversation, JobDescription
from app.models.user import User

from langgraph.types import Command

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

def workflow_config(conversation_id: UUID) -> dict:
    return {
        "configurable": {
            "thread_id": f"jd:{conversation_id}",
        },
        "recursion_limit": 50,
    }


def conversation_response(conversation: JDConversation, snapshot) -> dict:
    state = snapshot.values

    pauses = [
        pause
        for task in snapshot.tasks
        for pause in task.interrupts
    ]

    pending = None
    if pauses:
        pending = {
            "id": pauses[0].id,
            "payload": pauses[0].value,
        }

    checkpoint_id = None
    if snapshot.config:
        checkpoint_id = snapshot.config.get(
            "configurable", {}
        ).get("checkpoint_id")

    messages = [
        {
            "id": message.id,
            "role": "user" if message.type == "human" else "assistant",
            "content": message.content,
        }
        for message in state.get("messages", [])
        if message.type in {"human", "ai"}
        and isinstance(message.content, str)
    ]

    return {
        "conversation_id": str(conversation.id),
        "created_at": conversation.created_at,
        "checkpoint_id": checkpoint_id,
        "status": state.get("status", "not_started"),
        "messages": messages,
        "interrupt": pending,
        "draft_jd": state.get("draft_jd"),
        "approved_jd": state.get("approved_jd"),
        "revision_count": state.get("revision_count", 0),
        "error_code": state.get("error_code"),
    }


async def get_owned_conversation(
    db,
    recruiter_id: UUID,
    conversation_id: UUID,
    *,
    lock: bool = False,
) -> JDConversation:
    query = select(JDConversation).where(
        JDConversation.id == conversation_id,
        JDConversation.recruiter_id == recruiter_id,
    )

    if lock:
        query = query.with_for_update()

    conversation = await db.scalar(query)

    if conversation is None:
        raise HTTPException(
            status_code=404,
            detail="Conversation not found",
        )

    return conversation


@router.post("/conversations", status_code=201)
async def create_conversation(
    body: ConversationCreate,
    request: Request,
    user: Recruiter,
    db: DatabaseSession,
):
    conversation = JDConversation(recruiter_id=user.id)
    db.add(conversation)
    await db.commit()

    # Serialize changes to this conversation using a database row lock.
    await db.refresh(conversation, with_for_update=True)

    graph = request.app.state.jd_graph
    config = workflow_config(conversation.id)

    await run_in_threadpool(
        graph.invoke,
        {"messages": [HumanMessage(content=body.message)]},
        config,
    )

    snapshot = await run_in_threadpool(graph.get_state, config)
    response = conversation_response(conversation, snapshot)

    await db.commit()
    return response


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    request: Request,
    user: Recruiter,
    db: DatabaseSession,
):
    conversation = await get_owned_conversation(
        db,
        user.id,
        conversation_id,
    )

    snapshot = await run_in_threadpool(
        request.app.state.jd_graph.get_state,
        workflow_config(conversation.id),
    )

    document = await db.scalar(
        select(JobDescription).where(
            JobDescription.conversation_id == conversation.id,
        )
    )

    response = conversation_response(conversation, snapshot)
    response["job_description"] = (
        description_summary(document) if document else None
    )

    return response

def extract_jd_title(content: str) -> str:
    lines = content.strip().splitlines()

    if not lines or not lines[0].startswith("# "):
        raise HTTPException(
            status_code=422,
            detail="Draft must begin with a Markdown job title.",
        )

    title = lines[0][2:].strip()

    if not title or len(title) > 255:
        raise HTTPException(
            status_code=422,
            detail="Job title must contain 1–255 characters.",
        )

    return title


def description_summary(document: JobDescription) -> dict:
    return {
        "id": str(document.id),
        "title": document.title,
        "created_at": document.created_at,
    }


async def save_approved_jd(
    db,
    conversation: JDConversation,
    state: dict,
) -> JobDescription | None:
    if state.get("status") != "approved":
        return None

    existing = await db.scalar(
        select(JobDescription).where(
            JobDescription.conversation_id == conversation.id,
        )
    )

    if existing is not None:
        return existing

    content = state["approved_jd"]

    document = JobDescription(
        conversation_id=conversation.id,
        title=extract_jd_title(content),
        content=content,
        requirements=state["requirements"],
    )

    db.add(document)
    await db.flush()
    await db.refresh(document)

    return document


@router.post("/conversations/{conversation_id}/actions")
async def act_on_conversation(
    conversation_id: UUID,
    body: ConversationAction,
    request: Request,
    user: Recruiter,
    db: DatabaseSession,
):
    conversation = await get_owned_conversation(
        db,
        user.id,
        conversation_id,
        lock=True,
    )

    graph = request.app.state.jd_graph
    config = workflow_config(conversation.id)
    snapshot = await run_in_threadpool(graph.get_state, config)

    # Retrying approval can recover a save interrupted after the graph
    # approved the JD. The existing record is reused.
    if (
        snapshot.values.get("status") == "approved"
        and body.action == "approve"
    ):
        document = await save_approved_jd(
            db,
            conversation,
            snapshot.values,
        )

        response = conversation_response(conversation, snapshot)
        response["job_description"] = description_summary(document)

        await db.commit()
        return response

    current = conversation_response(conversation, snapshot)
    pending = current["interrupt"]

    if (
        pending is None
        or body.checkpoint_id != current["checkpoint_id"]
        or body.interrupt_id != pending["id"]
    ):
        raise HTTPException(
            status_code=409,
            detail="Conversation changed. Reload it before continuing.",
        )

    payload = pending["payload"]

    if body.action not in payload.get("allowed_actions", []):
        raise HTTPException(
            status_code=422,
            detail="This action is unavailable at the current stage.",
        )

    decision = {"action": body.action}

    if body.action in {"reply", "revise"}:
        if not body.message:
            raise HTTPException(
                status_code=422,
                detail="A reply or revision requires a message.",
            )

        key = "message" if body.action == "reply" else "feedback"
        decision[key] = body.message

    if body.action == "approve":
        # Check the title before committing approval in the graph.
        extract_jd_title(snapshot.values["draft_jd"])

    await run_in_threadpool(
        graph.invoke,
        Command(resume=decision),
        config,
    )

    snapshot = await run_in_threadpool(graph.get_state, config)

    document = await save_approved_jd(
        db,
        conversation,
        snapshot.values,
    )

    response = conversation_response(conversation, snapshot)
    response["job_description"] = (
        description_summary(document) if document else None
    )

    await db.commit()
    return response


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