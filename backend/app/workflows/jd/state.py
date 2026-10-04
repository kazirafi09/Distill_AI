from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


JDStatus = Literal[
    "gathering",
    "drafting",
    "awaiting_review",
    "approved",
    "cancelled",
    "failed",
]


class JDState(TypedDict):
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    requirements: NotRequired[str]
    requirements_ready: NotRequired[bool]
    follow_up_question: NotRequired[str]

    draft_jd: NotRequired[str]
    feedback: NotRequired[str]
    revision_count: NotRequired[int]
    status: NotRequired[JDStatus]
    approved_jd: NotRequired[str]
    error_code: NotRequired[str | None]