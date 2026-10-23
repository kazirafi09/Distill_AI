"""F11 analysis topology; shared CV graphs remain the processing implementation."""
from langgraph.graph import END, START, StateGraph

from app.workflows.wind_tunnel.nodes import build_nodes
from app.workflows.wind_tunnel.state import AnalysisState


def build_wind_tunnel_graph(run_id, owner_id, token, *, root, session_factory, saver=False, include_suggestions=False):
    nodes = build_nodes(run_id, owner_id, token, root=root, session_factory=session_factory, saver=saver,
                        include_suggestions=include_suggestions)
    graph = StateGraph(AnalysisState)
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_edge(START, nodes[0][0])
    for (left, _), (right, _) in zip(nodes, nodes[1:]):
        if include_suggestions and left == "load_snapshot":
            graph.add_conditional_edges(left, lambda s: "generate_suggestions" if s.get("reuse_analysis") else "generate_suite",
                                        ["generate_suggestions", "generate_suite"])
        elif include_suggestions and left == "validate_suggestions":
            graph.add_conditional_edges(left, lambda s: "generate_suggestions" if s.get("suggestion_status") == "repair" else "persist_suggestions",
                                        ["generate_suggestions", "persist_suggestions"])
        else:
            graph.add_edge(left, right)
    graph.add_edge(nodes[-1][0], END)
    return graph.compile(checkpointer=saver)
