import json

from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from app.workflows.jd.nodes import (
    gather_requirements,
    generate_jd,
    receive_requirements,
    record_reference_results,
    review_jd,
    revise_jd,
)
from app.workflows.jd.state import JDState
from app.workflows.jd.tools import JD_TOOLS


def has_tool_call(state: JDState) -> bool:
    messages = state.get("tool_messages", [])
    return bool(
        messages
        and isinstance(messages[-1], AIMessage)
        and messages[-1].tool_calls
    )


def route_start(state: JDState) -> str:
    return "gather_requirements"


def route_after_gathering(state: JDState) -> str:
    if state.get("status") == "failed":
        return END
    if has_tool_call(state):
        return "reference_tools"
    if state.get("requirements_ready"):
        return "generate_jd"
    return "receive_requirements"


def route_after_reply(state: JDState) -> str:
    return "gather_requirements" if state.get("status") == "gathering" else END


def route_after_generation(state: JDState) -> str:
    if state.get("status") == "failed":
        return END
    if has_tool_call(state):
        return "reference_tools"
    return "review_jd" if state.get("status") == "awaiting_review" else END


def route_after_review(state: JDState) -> str:
    return "gather_requirements" if state.get("status") == "gathering" else END


def route_after_tools(state: JDState) -> str:
    if state.get("status") == "failed":
        return END
    destination = state.get("tool_return_node")
    if destination not in {"gather_requirements", "generate_jd", "revise_jd"}:
        raise ValueError("Invalid tool return destination.")
    return destination


def build_jd_graph(checkpointer):
    builder = StateGraph(JDState)
    builder.add_node("gather_requirements", gather_requirements)
    builder.add_node("receive_requirements", receive_requirements)
    builder.add_node("generate_jd", generate_jd)
    builder.add_node("review_jd", review_jd)
    builder.add_node("revise_jd", revise_jd)
    builder.add_node(
        "reference_tools",
        ToolNode(
            JD_TOOLS,
            messages_key="tool_messages",
            handle_tool_errors=json.dumps({
                "query": "", "context": [], "sources": [],
                "rag_status": "failed",
                "error_code": "REFERENCE_TOOL_EXECUTION_FAILED",
            }),
        ),
    )
    builder.add_node("record_reference_results", record_reference_results)

    builder.add_conditional_edges(START, route_start, {
        "gather_requirements": "gather_requirements",
        "generate_jd": "generate_jd",
    })
    builder.add_conditional_edges("gather_requirements", route_after_gathering, {
        "reference_tools": "reference_tools",
        "generate_jd": "generate_jd",
        "receive_requirements": "receive_requirements",
        END: END,
    })
    builder.add_conditional_edges("receive_requirements", route_after_reply, {
        "gather_requirements": "gather_requirements", END: END,
    })
    generation_routes = {
        "reference_tools": "reference_tools", "review_jd": "review_jd", END: END,
    }
    builder.add_conditional_edges(
        "generate_jd", route_after_generation, generation_routes,
    )
    builder.add_conditional_edges(
        "revise_jd", route_after_gathering, {
            "reference_tools": "reference_tools", "generate_jd": "generate_jd",
            "receive_requirements": "receive_requirements", END: END,
        },
    )
    builder.add_conditional_edges("review_jd", route_after_review, {
        "gather_requirements": "gather_requirements", END: END,
    })
    builder.add_edge("reference_tools", "record_reference_results")
    builder.add_conditional_edges("record_reference_results", route_after_tools, {
        "gather_requirements": "gather_requirements",
        "generate_jd": "generate_jd",
        "revise_jd": "revise_jd",
        END: END,
    })
    return builder.compile(checkpointer=checkpointer)
