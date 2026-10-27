"""F5 topology, following the assistant workflow's graph/nodes/state layout."""

from langgraph.graph import END, START, StateGraph

from app.workflows.interview_analysis import nodes
from app.workflows.interview_analysis.state import InterviewAnalysisState


def after_load(state):
    return "segment_qa_pairs" if state.get("status") == "running" else "persist_analysis"


def after_grouping(state):
    return "classify_requirements" if state.get("status") == "running" else "persist_analysis"


def after_mapping(state):
    return "prepare_answer" if state.get("status") == "running" else "persist_analysis"


def after_merge(state):
    return "save_answer" if state.get("status") == "running" else "persist_analysis"


def after_prepare(state):
    if state.get("status") != "running":
        return "persist_analysis"
    return ["evaluate_relevance", "evaluate_accuracy", "evaluate_completeness", "evaluate_articulation"]


def after_save(state):
    limit = state.get("group_limit", 0) or len(state["groups"])
    if state["group_index"] < min(limit, len(state["groups"])):
        return "prepare_answer"
    return "detect_requirement_gaps"


def build_interview_analysis_graph(checkpointer=None):
    builder = StateGraph(InterviewAnalysisState)
    for name in ("load_transcript", "segment_qa_pairs", "classify_requirements", "prepare_answer",
                 "evaluate_relevance", "evaluate_accuracy", "evaluate_completeness", "evaluate_articulation",
                 "merge_evaluation", "save_answer", "detect_requirement_gaps", "persist_analysis"):
        builder.add_node(name, getattr(nodes, name))
    builder.add_edge(START, "load_transcript")
    builder.add_conditional_edges("load_transcript", after_load, ["segment_qa_pairs", "persist_analysis"])
    builder.add_conditional_edges("segment_qa_pairs", after_grouping, ["classify_requirements", "persist_analysis"])
    builder.add_conditional_edges("classify_requirements", after_mapping, ["prepare_answer", "persist_analysis"])
    evaluators = ["evaluate_relevance", "evaluate_accuracy", "evaluate_completeness", "evaluate_articulation"]
    builder.add_conditional_edges("prepare_answer", after_prepare, [*evaluators, "persist_analysis"])
    # A list of source nodes creates a barrier: merge waits for all four.
    builder.add_edge(evaluators, "merge_evaluation")
    builder.add_conditional_edges("merge_evaluation", after_merge, ["save_answer", "persist_analysis"])
    builder.add_conditional_edges("save_answer", after_save, ["prepare_answer", "detect_requirement_gaps"])
    builder.add_edge("detect_requirement_gaps", "persist_analysis")
    builder.add_edge("persist_analysis", END)
    return builder.compile(checkpointer=checkpointer)
