from uuid import UUID

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.models.assistant import AssistantConversation
from app.models.jd import JDConversation, JobDescription
from app.models.interviews import InterviewSession
from app.models.cv import Candidate, CandidateApplication
from app.services.jd.conversations import (
    act_on_jd_conversation,
    initialize_jd_conversation,
    load_jd_conversation,
)
from app.workflows.jd.intent import interpret_jd_message


def assistant_config(conversation_id: UUID, user_id: UUID) -> dict:
    return {
        "configurable": {
            "thread_id": f"assistant:{conversation_id}",
            "user_id": str(user_id),
        },
        "recursion_limit": 20,
    }


def checkpoint_id(snapshot) -> str | None:
    return (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")


async def get_owned_assistant(db, user_id, conversation_id, *, lock=False):
    query = select(AssistantConversation).where(
        AssistantConversation.id == conversation_id,
        AssistantConversation.user_id == user_id,
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    conversation = await db.scalar(query)
    if conversation is None:
        raise HTTPException(404, "Assistant conversation not found")
    return conversation


async def linked_jd(db, jd_graph, conversation):
    if conversation.jd_conversation_id is None:
        return None
    return await load_jd_conversation(
        db, jd_graph, conversation.user_id, conversation.jd_conversation_id,
    )


def context_updates(jd):
    if jd is None:
        return {"mode": "chat", "jd_conversation_id": None, "job_description_id": None}
    document = jd.get("job_description")
    active = jd["status"] in {"gathering", "drafting", "awaiting_review", "failed"}
    # An approved checkpoint without a saved document still needs save recovery.
    active = active or (jd["status"] == "approved" and not document)
    return {
        "mode": "jd_workflow" if active else "chat",
        "jd_conversation_id": jd["conversation_id"],
        "job_description_id": document["id"] if document else None,
    }


async def assistant_response(db, assistant_graph, jd_graph, conversation):
    snapshot = await run_in_threadpool(
        assistant_graph.get_state,
        assistant_config(conversation.id, conversation.user_id),
    )
    jd = await linked_jd(db, jd_graph, conversation)
    messages = [
        {
            "id": message.id,
            "role": "user" if message.type == "human" else "assistant",
            "content": message.content,
            **({"jd_after_message_id": message.additional_kwargs["jd_after_message_id"]}
               if "jd_after_message_id" in message.additional_kwargs else {}),
        }
        for message in snapshot.values.get("messages", [])
        if message.type in {"human", "ai"}
        and isinstance(message.content, str)
        and message.content.strip()
        and not getattr(message, "tool_calls", None)
    ]
    return {
        "conversation_id": str(conversation.id),
        "created_at": conversation.created_at,
        "checkpoint_id": checkpoint_id(snapshot),
        **context_updates(jd),
        "messages": messages,
        "error_code": snapshot.values.get("error_code"),
        "jd_conversation": jd,
    }


async def create_assistant_conversation(db, user_id):
    conversation = AssistantConversation(user_id=user_id)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def list_assistant_conversations(db, graph, user_id, *, limit=20, offset=0):
    conversations = list(await db.scalars(select(AssistantConversation).where(
        AssistantConversation.user_id == user_id,
    ).order_by(AssistantConversation.created_at.desc(), AssistantConversation.id.desc()).limit(limit).offset(offset)))
    items = []
    for conversation in conversations:
        snapshot = await run_in_threadpool(graph.get_state, assistant_config(conversation.id, user_id))
        title = next((m.content for m in snapshot.values.get("messages", [])
                      if isinstance(m, HumanMessage) and isinstance(m.content, str) and m.content.strip()), "New conversation")
        items.append({"conversation_id": str(conversation.id), "created_at": conversation.created_at,
                      "title": title[:100]})
    return {"items": items, "limit": limit, "offset": offset}


async def start_linked_jd(db, jd_graph, user_id, assistant_id, message):
    assistant = await get_owned_assistant(db, user_id, assistant_id, lock=True)
    if assistant.jd_conversation_id is None:
        jd = JDConversation(recruiter_id=user_id)
        db.add(jd)
        await db.flush()
        assistant.jd_conversation_id = jd.id
    jd_id = assistant.jd_conversation_id
    await db.commit()
    return await initialize_jd_conversation(db, jd_graph, user_id, jd_id, message)


async def complete_jd_handoff(db, assistant_graph, jd_graph, user_id, assistant_id):
    config = assistant_config(assistant_id, user_id)
    snapshot = await run_in_threadpool(assistant_graph.get_state, config)
    if not snapshot.values.get("jd_start_requested"):
        return
    message = next((
        m.content for m in reversed(snapshot.values.get("messages", []))
        if isinstance(m, HumanMessage) and isinstance(m.content, str)
    ), None)
    if not message:
        raise HTTPException(409, "The JD request is missing. Reload the conversation.")
    await start_linked_jd(db, jd_graph, user_id, assistant_id, message)
    assistant = await get_owned_assistant(db, user_id, assistant_id, lock=True)
    jd = await linked_jd(db, jd_graph, assistant)
    await run_in_threadpool(
        assistant_graph.update_state, config,
        {**context_updates(jd), "jd_start_requested": False, "error_code": None},
        as_node="assistant_tools",
    )
    await db.commit()


async def send_assistant_message(
    db, assistant_graph, jd_graph, user_id, assistant_id, message, checkpoint_id,
    *, session_id: UUID | None = None, interview_session_id: UUID | None = None,
):
    assistant = await get_owned_assistant(db, user_id, assistant_id, lock=True)
    config = assistant_config(assistant_id, user_id)
    if session_id is not None:
        config["configurable"]["auth_session_id"] = str(session_id)
    if interview_session_id is not None:
        document = await db.scalar(
            select(JobDescription)
            .join(JDConversation, JobDescription.conversation_id == JDConversation.id)
            .join(InterviewSession, InterviewSession.job_description_id == JobDescription.id)
            .where(
                InterviewSession.id == interview_session_id,
                InterviewSession.recruiter_id == user_id,
                JDConversation.recruiter_id == user_id,
            )
        )
        if document is None:
            raise HTTPException(404, "Interview session not found.")
        config["configurable"]["interview_session_id"] = str(interview_session_id)
        candidate_name = await db.scalar(
            select(Candidate.full_name)
            .join(CandidateApplication, CandidateApplication.candidate_id == Candidate.id)
            .join(InterviewSession, InterviewSession.application_id == CandidateApplication.id)
            .where(InterviewSession.id == interview_session_id, Candidate.recruiter_id == user_id)
        )
        config["configurable"]["interview_context"] = {
            "job_title": document.title, "candidate_name": candidate_name,
        }
        config["configurable"]["saved_jd_content"] = (
            f"Selected interview job title: {document.title}\n\n{document.content}"
        )
    snapshot = await run_in_threadpool(assistant_graph.get_state, config)
    if snapshot.next or snapshot.values.get("jd_start_requested"):
        if interview_session_id is not None:
            raise HTTPException(409, "Finish the pending conversation request before using interview chat.")
        if snapshot.next:
            await run_in_threadpool(assistant_graph.invoke, None, config)
        await complete_jd_handoff(db, assistant_graph, jd_graph, user_id, assistant_id)
        await db.commit()
        raise HTTPException(409, "Previous request recovered. Reload before sending again.")
    current = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
    if checkpoint_id != current:
        raise HTTPException(409, "Conversation changed. Reload it before continuing.")
    jd = await linked_jd(db, jd_graph, assistant)
    updates = context_updates(jd)
    if interview_session_id is not None and updates["mode"] == "jd_workflow":
        raise HTTPException(409, "Finish or cancel the active JD workflow before using this conversation for an interview.")
    if updates["mode"] == "jd_workflow":
        try:
            chat_context = [{"role": m.type, "content": m.content} for m in snapshot.values.get("messages", [])
                            if m.type in {"human", "ai"} and isinstance(m.content, str) and not getattr(m, "tool_calls", None)]
            intent = await run_in_threadpool(interpret_jd_message, message, {**jd, "central_messages": chat_context})
        except Exception:
            # A failed interpretation must never turn a cancel/question into
            # a destructive draft revision. Preserve the workflow for retry.
            intent = None
        if intent is None or intent.action == "chat":
            answer = intent.response if intent else "I could not interpret that message. Please try again; your draft is unchanged."
            anchor = next((m["id"] for m in reversed(jd.get("messages", [])) if m.get("id")), "jd-end")
            await run_in_threadpool(assistant_graph.update_state, config,
                {"messages": [HumanMessage(content=message, additional_kwargs={"jd_after_message_id": anchor}),
                              AIMessage(content=answer, additional_kwargs={"jd_after_message_id": anchor})]},
                as_node="assistant_tools")
            await db.commit()
            return await assistant_response(db, assistant_graph, jd_graph, assistant)
        action = intent.action
        if action in {"reply", "revise"}:
            action = "reply" if jd["status"] == "gathering" else "revise"
        if action == "approve":
            errors = jd.get("acceptance_errors", [])
            if jd["status"] == "gathering":
                errors = ["Provide the job role so I can generate a draft for review first."]
            if jd["status"] == "failed":
                errors = ["The latest draft attempt failed. Ask me to retry or cancel this workflow."]
            if errors:
                await run_in_threadpool(
                    assistant_graph.update_state, config,
                    {"messages": [HumanMessage(content=message), AIMessage(content=(
                        "Before I can accept and save this JD, please resolve:\n\n"
                        + "\n".join(f"- {error}" for error in errors)
                    ))]},
                    as_node="assistant_tools",
                )
                await db.commit()
                return await assistant_response(db, assistant_graph, jd_graph, assistant)
        pending = jd.get("interrupt")
        return await send_assistant_jd_action(
            db, assistant_graph, jd_graph, user_id, assistant_id,
            checkpoint_id=checkpoint_id, jd_checkpoint_id=jd["checkpoint_id"],
            interrupt_id=pending["id"] if pending else "workflow-recovery",
            action=action, message=message,
        )
    if interview_session_id is None and jd and jd.get("job_description"):
        # Fetch approved content from the ownership-checked database association.
        document = await db.scalar(select(JobDescription).where(
            JobDescription.conversation_id == assistant.jd_conversation_id,
        ))
        config["configurable"]["saved_jd_content"] = document.content
    await run_in_threadpool(
        assistant_graph.invoke,
        {**updates, "messages": [HumanMessage(content=message)],
         "jd_start_requested": False, "error_code": None},
        config,
    )
    await complete_jd_handoff(db, assistant_graph, jd_graph, user_id, assistant_id)
    await db.commit()
    return await assistant_response(db, assistant_graph, jd_graph, assistant)


async def send_assistant_jd_action(
    db, assistant_graph, jd_graph, user_id, assistant_id, *, checkpoint_id,
    jd_checkpoint_id, interrupt_id, action, message=None,
):
    assistant = await get_owned_assistant(db, user_id, assistant_id, lock=True)
    if assistant.jd_conversation_id is None:
        raise HTTPException(422, "This assistant conversation has no JD workflow.")
    config = assistant_config(assistant_id, user_id)
    snapshot = await run_in_threadpool(assistant_graph.get_state, config)
    current = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
    if checkpoint_id != current:
        raise HTTPException(409, "Conversation changed. Reload it before continuing.")
    result = await act_on_jd_conversation(
        db, jd_graph, user_id, assistant.jd_conversation_id,
        checkpoint_id=jd_checkpoint_id, interrupt_id=interrupt_id,
        action=action, message=message,
        chat_context=[{"role": m.type, "content": m.content} for m in snapshot.values.get("messages", [])
                      if m.type in {"human", "ai"} and isinstance(m.content, str) and not getattr(m, "tool_calls", None)],
    )
    assistant = await get_owned_assistant(db, user_id, assistant_id, lock=True)
    snapshot = await run_in_threadpool(assistant_graph.get_state, config)
    updates = {**context_updates(result), "jd_start_requested": False, "error_code": None}
    document = result.get("job_description")
    action_messages = [HumanMessage(content=message)] if message and action in {"approve", "cancel"} else []
    if document and snapshot.values.get("job_description_id") != document["id"]:
        action_messages.append(AIMessage(content=(
            f"Approved job description saved: {document['title']}. "
            f"It appears as Job #{document['number']} in Your Job Descriptions. "
            "You can now ask questions about it or ask to post it in Discord."
        )))
    elif result["status"] == "cancelled" and snapshot.values.get("mode") == "jd_workflow":
        action_messages.append(AIMessage(content="The JD workflow was cancelled."))
    if action_messages:
        updates["messages"] = action_messages
    await run_in_threadpool(
        assistant_graph.update_state, config, updates, as_node="assistant_tools",
    )
    await db.commit()
    return await assistant_response(db, assistant_graph, jd_graph, assistant)


async def open_saved_jd(db, assistant_graph, jd_graph, user_id, description_id):
    # Serialize association creation on the original JD conversation.
    document = await db.scalar(
        select(JobDescription).join(
            JDConversation, JobDescription.conversation_id == JDConversation.id,
        ).where(
            JobDescription.id == description_id,
            JDConversation.recruiter_id == user_id,
        ).with_for_update(of=JDConversation)
    )
    if document is None:
        raise HTTPException(404, "Job description not found")
    assistant = await db.scalar(select(AssistantConversation).where(
        AssistantConversation.jd_conversation_id == document.conversation_id,
    ))
    if assistant is None:
        assistant = AssistantConversation(
            user_id=user_id, jd_conversation_id=document.conversation_id,
        )
        db.add(assistant)
        await db.flush()
    elif assistant.user_id != user_id:
        raise HTTPException(404, "Assistant conversation not found")
    assistant_id = assistant.id
    await db.commit()
    assistant = await get_owned_assistant(db, user_id, assistant_id)
    return await assistant_response(db, assistant_graph, jd_graph, assistant)
