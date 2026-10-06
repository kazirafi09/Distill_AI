from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


JDStatus = Literal[
    "gathering", "drafting", "awaiting_review", "approved", "cancelled", "failed",
]


class ReferenceSource(TypedDict):
    reference_id: str
    filename: str
    document_type: str
    chunk_index: int
    distance: float


class JDState(TypedDict):
    messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    tool_messages: NotRequired[Annotated[list[AnyMessage], add_messages]]
    tool_return_node: NotRequired[
        Literal["gather_requirements", "generate_jd", "revise_jd"]
    ]
    tool_rounds: NotRequired[int]
    requirements: NotRequired[str]
    requirements_ready: NotRequired[bool]
    follow_up_question: NotRequired[str]
    draft_jd: NotRequired[str]
    feedback: NotRequired[str]
    revision_count: NotRequired[int]
    status: NotRequired[JDStatus]
    approved_jd: NotRequired[str]
    error_code: NotRequired[str | None]
    reference_context: NotRequired[str]
    reference_sources: NotRequired[list[ReferenceSource]]
    rag_status: NotRequired[str]
