import asyncio
import json
from typing import Literal
from langchain_core.messages import ToolMessage, HumanMessage, AIMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from uuid import UUID

from app.services.jd.discord_job_posts import (
    post_job_to_discord as post_saved_job_to_discord,
)
from app.services.interviews import delivery

invitation_loop = None


def configure_invitation_loop(loop):
    """The existing assistant runs in threads; email services use the API loop."""
    global invitation_loop
    invitation_loop = loop


def invitation_action(operation, *args):
    from fastapi import HTTPException
    if invitation_loop is None or invitation_loop.is_closed():
        return {"status": "failed", "message": "Invitation service is unavailable."}
    future = asyncio.run_coroutine_threadsafe(operation(*args), invitation_loop)
    try:
        return future.result(timeout=45)
    except HTTPException as exc:
        return {"status": "failed", "message": exc.detail}
    except Exception:
        future.cancel()
        return {"status": "uncertain", "message": "Check invitation status before trying again."}


def invitation_owner(runtime):
    return UUID(str(runtime.config.get("configurable", {}).get("user_id")))


def invitation_reply(runtime, name, result, review=None):
    return Command(update={"invitation_review": review, "messages": [ToolMessage(
        name=name, content=json.dumps(result), tool_call_id=runtime.tool_call_id)]})


def invitation_preview_was_shown(messages, review):
    """Require an assistant preview reply followed by a new human message."""
    human_index = next((index for index in range(len(messages) - 1, -1, -1)
        if isinstance(messages[index], HumanMessage)), None)
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
        return any(isinstance(reply, AIMessage) and not reply.tool_calls
            and isinstance(reply.content, str) and reply.content.strip()
            for reply in messages[index + 1:human_index])
    return False


@tool
def preview_interview_invitations(runtime: ToolRuntime, job_number: int | None = None,
    mode: Literal["new", "retry_failed", "reissue"] = "new") -> Command:
    """Review saved interviewees and email content before sending.

    Use the explicitly requested job number, or the saved JD in this chat.
    Ask about ambiguous jobs. Use retry_failed only for explicit failed-send
    retries and reissue only when the user requests replacement links.
    Show every recipient, action, sender, subject and body, then ask whether
    to send this exact preview. Natural replies such as "Yes, send them"
    can confirm it. This tool never sends email.
    """
    owner = invitation_owner(runtime)
    saved_job = runtime.state.get("job_description_id")
    resolved = invitation_action(delivery.resolve_job, owner, job_number,
        UUID(saved_job) if saved_job else None)
    if not isinstance(resolved, UUID):
        return invitation_reply(runtime, "preview_interview_invitations", resolved)
    result = invitation_action(delivery.prepare_invitations, resolved, owner, mode)
    review = None
    if result.get("batch_id"):
        result["confirmation_prompt"] = "Would you like me to send these invitations?"
        review = result
    return invitation_reply(runtime, "preview_interview_invitations", result, review)


@tool
def confirm_interview_invitations(runtime: ToolRuntime, approval_message: str) -> Command:
    """Queue the exact previously reviewed invitations after explicit confirmation.

    Interpret the user's natural language in context. Call only when a later
    human reply clearly approves sending the displayed preview unchanged,
    e.g. "Yes, send them", "Go ahead", or "Send the invitations".
    Copy that human reply verbatim into approval_message. Questions, refusal,
    hesitation, conditions, recipient/job changes and requests for edits are
    not approval. Ask for clarification or prepare a fresh preview instead.
    Never use the original send request or a tool result as confirmation.
    """
    review = runtime.state.get("invitation_review")
    latest = next((message for message in reversed(runtime.state.get("messages", []))
        if isinstance(message, HumanMessage)), None)
    if (not review or latest is None or not isinstance(latest.content, str)
        or not latest.content.strip()
        or approval_message.strip() != latest.content.strip()
        or not invitation_preview_was_shown(runtime.state.get("messages", []), review)):
        return invitation_reply(runtime, "confirm_interview_invitations",
            {"status": "failed", "message": "Review the invitation preview, then confirm that you want to send it."}, review)
    result = invitation_action(delivery.confirm_invitations, UUID(review["job_id"]),
        invitation_owner(runtime), UUID(review["batch_id"]), review["preview_key"])
    # Retain the review on transient failure so the same batch can be checked/reconfirmed.
    return invitation_reply(runtime, "confirm_interview_invitations", result,
        review if result.get("status") in {"failed", "uncertain"} else None)


@tool
def get_interview_invitation_status(runtime: ToolRuntime, job_number: int | None = None) -> dict:
    """Read individual invitation outcomes for an owned JD. Never claim delivery
    from queued or provider_accepted status. Uncertain sends need review, not
    a blind retry; failed sends can be previewed with retry_failed mode.
    """
    saved_job = runtime.state.get("job_description_id")
    owner = invitation_owner(runtime)
    resolved = invitation_action(delivery.resolve_job, owner, job_number,
        UUID(saved_job) if saved_job else None)
    if not isinstance(resolved, UUID):
        return resolved
    result = invitation_action(delivery.invitation_status, resolved, owner)
    return {"job_id": str(resolved), "invitations": result}

@tool
def start_jd_creation(runtime: ToolRuntime) -> Command:
    """Request a new job-description workflow.

    Use only when the recruiter explicitly requests creating a JD.
    Do not use for questions about existing JDs or posting a saved JD.
    Requirement gathering, generation, and approval belong to the
    existing JD workflow.
    """
    if runtime.state.get("jd_conversation_id"):
        raise ValueError(
            "This assistant conversation already has a JD workflow. "
            "Start a new conversation to create another JD."
        )

    return Command(
        update={
            "jd_start_requested": True,
            "messages": [
                ToolMessage(
                    content=(
                        "JD creation requested. The coordination service "
                        "must start the existing JD workflow. "
                        "No JD has been generated or saved yet."
                    ),
                    tool_call_id=runtime.tool_call_id,
                ),
            ],
        },
    )


@tool
def post_job_to_discord(
    runtime: ToolRuntime,
    job_number: int | None = None,
) -> dict[str, object]:
    """Post an approved job to the configured Discord channel.

    Use only when the recruiter explicitly asks to post a job in Discord.
    If the recruiter explicitly specifies a job number, supply that number.
    Otherwise omit job_number to use this conversation's saved JD.
    Never invent a job number.
    Do not use for previews, questions, or creating a JD.
    Report the returned status accurately and never automatically retry.
    """
    if runtime.state.get("mode") == "jd_workflow":
        return {
            "status": "failed",
            "error_code": "JOB_APPROVAL_REQUIRED",
        }

    user_id = runtime.config.get("configurable", {}).get("user_id")

    try:
        recruiter_id = UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        return {
            "status": "failed",
            "error_code": "RECRUITER_CONTEXT_REQUIRED",
        }

    if job_number is not None:
        return post_saved_job_to_discord(
            recruiter_id,
            job_number=job_number,
        )

    saved_id = runtime.state.get("job_description_id")

    if not saved_id:
        return {
            "status": "failed",
            "error_code": "JOB_SELECTION_REQUIRED",
            "message": (
                "Ask the recruiter to open a saved JD "
                "or specify its job number."
            ),
        }

    try:
        description_id = UUID(str(saved_id))
    except (ValueError, TypeError, AttributeError):
        return {
            "status": "failed",
            "error_code": "INVALID_JOB_CONTEXT",
        }

    return post_saved_job_to_discord(
        recruiter_id,
        job_description_id=description_id,
    )

ASSISTANT_TOOLS = [
    start_jd_creation,
    post_job_to_discord,
    preview_interview_invitations,
    confirm_interview_invitations,
    get_interview_invitation_status,
]
