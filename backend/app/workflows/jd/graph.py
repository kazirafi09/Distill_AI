from langgraph.graph import END, START, StateGraph

from app.workflows.jd.nodes import (
    gather_requirements,
    generate_jd,
    receive_requirements,
    retrieve_references,
    review_jd,
    revise_jd,
)
from app.workflows.jd.state import JDState


def route_start(state: JDState) -> str:
    if state.get("messages"):
        return "gather_requirements"

    return "retrieve_references"


def route_after_gathering(state: JDState) -> str:
    if state.get("status") == "failed":
        return END

    if state.get("requirements_ready"):
        return "retrieve_references"

    return "receive_requirements"


def route_after_reply(state: JDState) -> str:
    if state.get("status") == "gathering":
        return "gather_requirements"

    return END


def route_after_retrieval(state: JDState) -> str:
    if state.get("status") == "failed":
        return END

    return "generate_jd"


def route_after_revision_retrieval(state: JDState) -> str:
    if state.get("status") == "failed":
        return END

    return "revise_jd"


def route_after_generation(state: JDState) -> str:
    if state.get("status") == "awaiting_review":
        return "review_jd"

    return END


def route_after_review(state: JDState) -> str:
    if state.get("status") == "drafting":
        return "retrieve_revision_references"

    return END


def build_jd_graph(checkpointer):
    builder = StateGraph(JDState)

    builder.add_node("gather_requirements", gather_requirements)
    builder.add_node("receive_requirements", receive_requirements)
    builder.add_node("retrieve_references", retrieve_references)
    builder.add_node(
        "retrieve_revision_references",
        retrieve_references,
    )
    builder.add_node("generate_jd", generate_jd)
    builder.add_node("review_jd", review_jd)
    builder.add_node("revise_jd", revise_jd)

    builder.add_conditional_edges(
        START,
        route_start,
        {
            "gather_requirements": "gather_requirements",
            "retrieve_references": "retrieve_references",
        },
    )

    builder.add_conditional_edges(
        "gather_requirements",
        route_after_gathering,
        {
            "retrieve_references": "retrieve_references",
            "receive_requirements": "receive_requirements",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "receive_requirements",
        route_after_reply,
        {
            "gather_requirements": "gather_requirements",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "retrieve_references",
        route_after_retrieval,
        {
            "generate_jd": "generate_jd",
            END: END,
        },
    )

    generation_routes = {
        "review_jd": "review_jd",
        END: END,
    }

    builder.add_conditional_edges(
        "generate_jd",
        route_after_generation,
        generation_routes,
    )

    builder.add_conditional_edges(
        "review_jd",
        route_after_review,
        {
            "retrieve_revision_references":
                "retrieve_revision_references",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "retrieve_revision_references",
        route_after_revision_retrieval,
        {
            "revise_jd": "revise_jd",
            END: END,
        },
    )

    builder.add_conditional_edges(
        "revise_jd",
        route_after_generation,
        generation_routes,
    )

    return builder.compile(checkpointer=checkpointer)