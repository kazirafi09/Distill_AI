from langchain_core.messages import AIMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from app.workflows.assistant.nodes import assistant_turn, execute_assistant_tool
from app.workflows.assistant.state import AssistantState


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
    builder.add_node("assistant_tools", execute_assistant_tool)

    builder.add_edge(START, "assistant")

    builder.add_conditional_edges(
        "assistant",
        route_after_assistant,
        {
            "assistant_tools": "assistant_tools",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "assistant_tools",
        route_after_tools,
        {
            "assistant": "assistant",
            END: END,
        },
    )

    return builder.compile(checkpointer=checkpointer)

def route_after_tools(state: AssistantState) -> str:
    if (
        state.get("error_code")
        or state.get("jd_start_requested")
        or state.get("mode") == "jd_workflow"
    ):
        return END

    messages = state.get("messages", [])

    if (
        messages
        and isinstance(messages[-1], ToolMessage)
    ):
        return "assistant"

    return END
