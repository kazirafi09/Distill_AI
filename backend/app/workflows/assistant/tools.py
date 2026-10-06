from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.prebuilt import ToolRuntime
from langgraph.types import Command


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


ASSISTANT_TOOLS = [start_jd_creation]