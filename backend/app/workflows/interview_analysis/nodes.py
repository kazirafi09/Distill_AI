"""Straightforward F5 node functions; the four evaluators write separate keys."""

from pathlib import Path

from app.services.interviews import analysis
from app.services.interviews.rubric import DIMENSIONS, RUBRIC_VERSION, dimension_summaries
from app.workflows.interview_analysis.state import InterviewAnalysisState


def failed(error):
    code = str(error) if isinstance(error, analysis.AnalysisError) else "ANALYSIS_INPUT_OR_EXECUTION_INVALID"
    technical = {"ANALYSIS_MODEL_REQUEST_FAILED", "ANALYSIS_MODEL_TIMEOUT", "MODEL_AUTH_FAILED",
                 "MODEL_RATE_LIMITED", "MODEL_NOT_AVAILABLE", "MODEL_REQUEST_INVALID", "MODEL_API_KEY_MISSING"}
    return {"status": "failed" if code in technical else "needs_review", "error_code": code}


async def load_transcript(state: InterviewAnalysisState):
    try:
        return analysis.prepare_input(state)
    except Exception as error:
        return failed(error)


async def segment_qa_pairs(state: InterviewAnalysisState):
    try:
        return await analysis.segment_questions(state)
    except Exception as error:
        return failed(error)


async def classify_requirements(state: InterviewAnalysisState):
    try:
        return {"groups": await analysis.classify_groups(state)}
    except Exception as error:
        return failed(error)


async def prepare_answer(state: InterviewAnalysisState):
    # Clear previous results before the next parallel superstep.
    return {"relevance": None, "accuracy": None, "completeness": None, "articulation": None}


async def evaluation(state, dimension):
    try:
        result = await analysis.evaluate_dimension(state, dimension)
    except Exception as error:
        result = {**failed(error)}
    return {dimension: result}


async def evaluate_relevance(state: InterviewAnalysisState):
    return await evaluation(state, "relevance")


async def evaluate_accuracy(state: InterviewAnalysisState):
    return await evaluation(state, "accuracy")


async def evaluate_completeness(state: InterviewAnalysisState):
    return await evaluation(state, "completeness")


async def evaluate_articulation(state: InterviewAnalysisState):
    return await evaluation(state, "articulation")


async def merge_evaluation(state: InterviewAnalysisState):
    values = [state.get(dimension) for dimension in DIMENSIONS]
    if any(not value or value.get("status") != "validated" for value in values):
        errors = [value for value in values if value and value.get("status") != "validated"]
        return {"status": "failed" if any(e.get("status") == "failed" for e in errors) else "needs_review",
                "error_code": next((e.get("error_code") for e in errors if e.get("error_code")), "EVALUATION_INCOMPLETE")}
    return {"merged_evaluation": {name: state[name]["result"] for name in DIMENSIONS}}


async def save_answer(state: InterviewAnalysisState):
    group = state["groups"][state["group_index"]]
    result = {"group": group, "evaluations": state["merged_evaluation"]}
    if state.get("storage_run_id"):
        from app.services.interviews.assessment_store import save_answer as save_stored_answer
        await save_stored_answer(state, result)
    else:
        analysis.write_json(Path(state["run_directory"]) / ("answer-" + group["id"] + ".json"), result)
    return {"results": [*state["results"], result], "group_index": state["group_index"] + 1}


async def detect_requirement_gaps(state: InterviewAnalysisState):
    completed = "completed_synthetic" if state["prepared"]["synthetic"] else "completed"
    return {"coverage": analysis.finalize_coverage(state),
            "status": completed if len(state["results"]) == len(state["groups"]) else "partial_synthetic"}


async def persist_analysis(state: InterviewAnalysisState):
    results = state.get("results", [])
    synthetic = state["prepared"].get("synthetic", True)
    warnings = ["MODEL_JUDGMENTS_REQUIRE_REVIEW"]
    if synthetic:
        warnings += ["SYNTHETIC_DEVELOPMENT_ONLY", "REAL_TRANSCRIPT_FINALIZATION_PENDING"]
    if not state.get("storage_run_id"):
        warnings += ["NO_PRODUCTION_DATABASE_OR_API_INTEGRATION"]
    artifact = {
        "schema": "distill-f5-stored-analysis-v1" if state.get("storage_run_id") else "distill-f5-development-analysis-v1",
        "run_id": state.get("run_id"),
        "synthetic": synthetic, "publishable": False, "f6_ready": False,
        "status": state.get("status", "needs_review"), "error_code": state.get("error_code"),
        "source_transcript_id": state["prepared"].get("source_transcript_id"),
        "source_status": state["prepared"].get("source_status"),
        "source_quality_flags": state["prepared"].get("source_quality_flags", []),
        "rubric_version": RUBRIC_VERSION, "model_version": state.get("model_version"),
        "criteria_snapshot": state.get("criteria", []),
        "groups": state.get("groups", []), "excluded_turns": state.get("excluded_turns", []),
        "results": results, "coverage": state.get("coverage", []),
        "dimension_summaries": dimension_summaries(results),
        "evaluated_groups": len(results), "total_groups": len(state.get("groups", [])),
        "warnings": warnings,
    }
    if state.get("recovery"):
        artifact["recovery"] = state["recovery"]
        artifact["warnings"].append("INHERITED_VALIDATED_STAGES_FROM_PRIOR_PROMPT")
    artifact["artifact_sha256"] = analysis.digest(artifact)
    if state.get("run_directory"):
        analysis.write_json(Path(state["run_directory"]) / "analysis.json", artifact)
    return {"artifact": artifact}
