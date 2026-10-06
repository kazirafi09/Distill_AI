from typing import Annotated, Literal, NotRequired, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


AssistantMode = Literal["chat", "jd_workflow"]


class AssistantState(TypedDict):
    messages: Annotated[list[AnyMessage], add_messages]

    mode: NotRequired[AssistantMode]

    jd_conversation_id: NotRequired[str | None]
    job_description_id: NotRequired[str | None]

    error_code: NotRequired[str | None]
    jd_start_requested: NotRequired[bool]