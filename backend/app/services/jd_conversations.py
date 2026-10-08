"""Shared JD operations used by HTTP routes and future assistant coordination.

Mutating operations retain the existing commit boundaries and row locking.
Call them with a dedicated request/session; they are not transaction-neutral.
The graph uses its own checkpoint connection, so approval retries still recover
an interrupted SQLAlchemy save.
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.jd import JDConversation, JobDescription
from app.services.screening_profiles import acceptance_errors


def validate_acceptance(state: dict) -> None:
    errors = acceptance_errors(state)
    if errors:
        raise HTTPException(status_code=422, detail=" ".join(errors))

def workflow_config(
    conversation_id: UUID,
    recruiter_id: UUID,
) -> dict:
    return {
        "configurable": {
            "thread_id": f"jd:{conversation_id}",
            "recruiter_id": str(recruiter_id),
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
        "screening_profile": state.get("screening_profile"),
        "profile_version": state.get("profile_version"),
        "acceptance_errors": acceptance_errors(state) if state.get("status") == "awaiting_review" else [],
        "error_code": state.get("error_code"),
        "rag_status": state.get("rag_status", "not_run"),
        "reference_sources": state.get("reference_sources", []),
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
        "number": document.number,
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

    validate_acceptance(state)
    content = state["approved_jd"]

    document = JobDescription(
        conversation_id=conversation.id,
        title=extract_jd_title(content),
        content=content,
        requirements=state["requirements"],
        screening_profile=state["screening_profile"],
        profile_version=state["profile_version"],
        google_form_url=state["screening_profile"]["google_form_url"],
    )

    db.add(document)
    await db.flush()
    await db.refresh(document)

    return document


async def create_jd_conversation(
    db: AsyncSession, graph: Any, recruiter_id: UUID, message: str,
) -> dict:
    conversation = JDConversation(recruiter_id=recruiter_id)
    db.add(conversation)
    await db.commit()
    return await initialize_jd_conversation(db, graph, recruiter_id, conversation.id, message)


async def initialize_jd_conversation(
    db: AsyncSession, graph: Any, recruiter_id: UUID,
    conversation_id: UUID, message: str,
) -> dict:
    conversation = await get_owned_conversation(db, recruiter_id, conversation_id, lock=True)
    config = workflow_config(conversation.id, recruiter_id)
    snapshot = await run_in_threadpool(graph.get_state, config)
    if not snapshot.values:
        await run_in_threadpool(graph.invoke, {"messages": [HumanMessage(content=message)]}, config)
    elif snapshot.next and not any(task.interrupts for task in snapshot.tasks):
        await run_in_threadpool(graph.invoke, None, config)
    snapshot = await run_in_threadpool(graph.get_state, config)
    response = conversation_response(conversation, snapshot)
    await db.commit()
    return response


async def load_jd_conversation(
    db: AsyncSession, graph: Any, recruiter_id: UUID, conversation_id: UUID,
) -> dict:
    conversation = await get_owned_conversation(
        db,
        recruiter_id,
        conversation_id,
    )

    snapshot = await run_in_threadpool(
        graph.get_state,
        workflow_config(conversation.id, recruiter_id),
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


async def act_on_jd_conversation(
    db: AsyncSession, graph: Any, recruiter_id: UUID, conversation_id: UUID,
    *, checkpoint_id: str, interrupt_id: str, action: str,
    message: str | None = None,
) -> dict:
    conversation = await get_owned_conversation(
        db,
        recruiter_id,
        conversation_id,
        lock=True,
    )

    config = workflow_config(conversation.id, recruiter_id)
    snapshot = await run_in_threadpool(graph.get_state, config)

    # Retrying approval can recover a save interrupted after the graph
    # approved the JD. The existing record is reused.
    if (
        snapshot.values.get("status") == "approved"
        and action == "approve"
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
        or checkpoint_id != current["checkpoint_id"]
        or interrupt_id != pending["id"]
    ):
        raise HTTPException(
            status_code=409,
            detail="Conversation changed. Reload it before continuing.",
        )

    payload = pending["payload"]

    if action not in payload.get("allowed_actions", []):
        raise HTTPException(
            status_code=422,
            detail="This action is unavailable at the current stage.",
        )

    decision = {"action": action}

    if action in {"reply", "revise"}:
        if not message:
            raise HTTPException(
                status_code=422,
                detail="A reply or revision requires a message.",
            )

        key = "message" if action == "reply" else "feedback"
        decision[key] = message

    if action == "approve":
        validate_acceptance(snapshot.values)
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
