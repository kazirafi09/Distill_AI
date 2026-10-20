from langgraph.graph import END, START, StateGraph
from app.workflows.assessment_consolidation import nodes
from app.workflows.assessment_consolidation.state import ConsolidationState


def after_load(state):
    return ["analyze_strengths", "analyze_weaknesses", "evidence_gaps", "recommended_areas"] if state.get("status") == "running" else "persist"


def after_merge(state):
    return "validate_traceability" if state.get("status") == "running" else "persist"


def after_validation(state):
    if not state.get("validation_error"):
        return "generate_report_data"
    return "repair_assessment" if state.get("repair_count", 0) < 2 else "persist"


def build_consolidation_graph(checkpointer=None):
    builder = StateGraph(ConsolidationState)
    names = ("load_all_results", "analyze_strengths", "analyze_weaknesses", "evidence_gaps", "recommended_areas",
        "merge_assessment", "validate_traceability", "repair_assessment", "generate_report_data", "persist")
    for name in names:
        builder.add_node(name, getattr(nodes, name))
    branches = ["analyze_strengths", "analyze_weaknesses", "evidence_gaps", "recommended_areas"]
    builder.add_edge(START, "load_all_results")
    builder.add_conditional_edges("load_all_results", after_load, [*branches, "persist"])
    builder.add_edge(branches, "merge_assessment")
    builder.add_conditional_edges("merge_assessment", after_merge, ["validate_traceability", "persist"])
    builder.add_conditional_edges("validate_traceability", after_validation, ["generate_report_data", "repair_assessment", "persist"])
    builder.add_conditional_edges("repair_assessment", after_merge, ["validate_traceability", "persist"])
    builder.add_edge("generate_report_data", "persist")
    builder.add_edge("persist", END)
    return builder.compile(checkpointer=checkpointer)
