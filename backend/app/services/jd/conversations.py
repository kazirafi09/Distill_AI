"""Shared JD operations used by HTTP routes and future assistant coordination.

Mutating operations retain the existing commit boundaries and row locking.
Call them with a dedicated request/session; they are not transaction-neutral.
The graph uses its own checkpoint connection, so approval retries still recover
an interrupted SQLAlchemy save.
"""

from typing import Any
from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.jd import JDConversation, JobDescription
from app.services.jd.screening_profiles import acceptance_errors, profile_errors
from app.workflows.jd.chat_routing import recruiter_context


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
            **({"draft_revision": message.additional_kwargs["jd_draft_revision"]}
               if "jd_draft_revision" in message.additional_kwargs else {}),
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
        "use_role_knowledge": state.get("use_role_knowledge", False),
        "acceptance_errors": acceptance_errors(state) if state.get("status") == "awaiting_review" else [],
        "screening_warnings": profile_errors(state.get("screening_profile")),
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
        screening_profile=state.get("screening_profile"),
        profile_version=state.get("profile_version"),
        google_form_url=(state.get("screening_profile") or {}).get("google_form_url"),
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
    *, recruiter_messages: list | None = None, chat_context: list[dict] | None = None,
) -> dict:
    conversation = await get_owned_conversation(db, recruiter_id, conversation_id, lock=True)
    config = workflow_config(conversation.id, recruiter_id)
    snapshot = await run_in_threadpool(graph.get_state, config)
    if not snapshot.values:
        await run_in_threadpool(graph.invoke, {
            "messages": [HumanMessage(content=m.content) for m in recruiter_messages]
                        if recruiter_messages else [HumanMessage(content=message)],
            "assistant_context": chat_context or [],
        }, config)
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

    # Older checkpoints saved each draft as state but not as a chat message.
    # Recover those suggestions without changing workflow state or invoking LLMs.
    values = snapshot.values
    if values:
        history = await run_in_threadpool(
            lambda: list(graph.get_state_history(workflow_config(conversation.id, recruiter_id))),
        )
        messages = list(values.get("messages", []))
        drafts = {}
        represented = {(m.additional_kwargs.get("jd_draft_revision"), m.content)
                       for m in messages if isinstance(m.content, str) and "jd_draft_revision" in m.additional_kwargs}
        for previous in reversed(history):
            content = previous.values.get("draft_jd")
            revision = previous.values.get("revision_count", 0)
            if content and (revision, content) not in represented and previous.values.get("status") in {"awaiting_review", "approved", "cancelled"}:
                drafts.setdefault((revision, content), previous.values.get("messages", []))
        for (revision, content), preceding in reversed(list(drafts.items())):
            anchor = preceding[-1].id if preceding else None
            position = next((i + 1 for i, m in enumerate(messages) if anchor and m.id == anchor), len(messages))
            messages.insert(position, AIMessage(content=content, additional_kwargs={"jd_draft_revision": revision}))
        snapshot = snapshot._replace(values={**values, "messages": messages})
    response = conversation_response(conversation, snapshot)
    response["job_description"] = (
        description_summary(document) if document else None
    )

    return response


async def act_on_jd_conversation(
    db: AsyncSession, graph: Any, recruiter_id: UUID, conversation_id: UUID,
    *, checkpoint_id: str, interrupt_id: str, action: str,
    message: str | None = None,
    chat_context: list[dict] | None = None,
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

    if snapshot.values.get("status") == "failed" and action in {"cancel", "reply", "revise"}:
        if checkpoint_id != current["checkpoint_id"]:
            raise HTTPException(409, "Conversation changed. Reload it before continuing.")
        if action == "cancel":
            await run_in_threadpool(graph.update_state, config,
                {"status": "cancelled", "error_code": None}, as_node="review_jd")
        else:
            if not message or not message.strip():
                raise HTTPException(422, "A retry requires a message.")
            await run_in_threadpool(graph.invoke,
                {"messages": [HumanMessage(content=message)], "status": "gathering",
                 "feedback": message, "error_code": None, "assistant_context": chat_context or []}, config)
        snapshot = await run_in_threadpool(graph.get_state, config)
        response = conversation_response(conversation, snapshot)
        await db.commit()
        return response

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
    context_update = {"assistant_context": chat_context} if chat_context is not None else {}

    if action in {"reply", "revise"}:
        if not message:
            raise HTTPException(
                status_code=422,
                detail="A reply or revision requires a message.",
            )

        key = "message" if action == "reply" else "feedback"
        decision[key] = message
        if action == "reply" and chat_context:
            # Recover old chats whose workflow started from only "save this JD".
            # Only user-authored facts become evidence; AI drafts stay references.
            existing = {m.content for m in snapshot.values.get("messages", [])
                        if isinstance(m, HumanMessage) and isinstance(m.content, str)}
            candidates = [HumanMessage(content=item["content"]) for item in chat_context
                          if item.get("role") in {"user", "human"}
                          and isinstance(item.get("content"), str)]
            missing = [m for m in recruiter_context(candidates) if m.content not in existing]
            if missing:
                context_update["messages"] = missing

    if action == "approve":
        validate_acceptance(snapshot.values)
        decision["approval_message"] = message or "Approved via JD review."
        # Check the title before committing approval in the graph.
        extract_jd_title(snapshot.values["draft_jd"])

    await run_in_threadpool(
        graph.invoke,
        Command(resume=decision, update=context_update or None),
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
