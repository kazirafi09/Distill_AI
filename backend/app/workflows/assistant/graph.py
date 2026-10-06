from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.workflows.assistant.nodes import assistant_turn
from app.workflows.assistant.state import AssistantState
from app.workflows.assistant.tools import ASSISTANT_TOOLS


def route_after_assistant(state: AssistantState) -> str:
    if state.get("error_code"):
        return END

    messages = state.get("messages", [])

    if (
        messages
        and isinstance(messages[-1], AIMessage)
        and messages[-1].tool_calls
    ):
        return "assistant_tools"

    return END


def build_assistant_graph(checkpointer=None):
    builder = StateGraph(AssistantState)

    builder.add_node("assistant", assistant_turn)
    builder.add_node(
        "assistant_tools",
        ToolNode(
            ASSISTANT_TOOLS,
            handle_tool_errors=False,
        ),
    )

    builder.add_edge(START, "assistant")

    builder.add_conditional_edges(
        "assistant",
        route_after_assistant,
        {
            "assistant_tools": "assistant_tools",
            END: END,
        },
    )

    # The current tool requests a JD handoff. Stop here so the
    # coordination service can start the existing JD workflow.
    builder.add_edge("assistant_tools", END)

    return builder.compile(checkpointer=checkpointer)