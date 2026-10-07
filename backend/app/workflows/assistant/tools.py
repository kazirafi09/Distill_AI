from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command
from uuid import UUID

from app.services.discord_job_posts import (
    post_job_to_discord as post_saved_job_to_discord,
)

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
]