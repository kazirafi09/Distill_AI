from pydantic import ValidationError
from app.services.interviews import analysis, consolidation as service, assessment_store as store
from app.services.interviews.consolidation_schemas import Findings, Suitability, AssessmentDraft
from app.workflows.interview_analysis.nodes import failed


async def load_all_results(state):
    try:
        return {"sources": service.build_sources(state["inputs"]), "status": "running", "error_code": None, "repair_count": 0}
    except Exception as error:
        return failed(error)


async def analyze(state, section):
    try:
        result = await service.request(state, "f6-" + section, Findings,
            "Produce ONLY the " + section + " branch. " + service.INSTRUCTIONS[section]
            + " Return findings; use an empty list if none is supported. For recommended_areas, write actionable "
            "follow-up checks (for example ask, verify or test), not descriptive strengths or weaknesses.",
            {**service.context(state), "requested_section": section})
    except Exception as error:
        result = {"execution_error": failed(error)}
    return {section: result}


async def analyze_strengths(state):
    return await analyze(state, "strengths")


async def analyze_weaknesses(state):
    return await analyze(state, "weaknesses")


async def evidence_gaps(state):
    return await analyze(state, "evidence_gaps")


async def recommended_areas(state):
    return await analyze(state, "recommended_areas")


async def merge_assessment(state):
    for section in service.BRANCHES:
        if "execution_error" in state[section]:
            return state[section]["execution_error"]
    try:
        findings = {section: Findings.model_validate(state[section]).model_dump()["findings"] for section in service.BRANCHES}
    except ValidationError:
        return {"draft": {section: state[section].get("findings") for section in service.BRANCHES}, "repair_count": 0}
    try:
        suitability = await service.request(state, "f6-merge", Suitability,
            "Merge the four finding branches into a qualitative suitability position and rationale. "
            "Cite source IDs. Missing coverage/unknown accuracy or unresolved evidence limits require insufficient_evidence. "
            "No hiring decision, combined score or changes to findings. Return the suitability schema.",
            {**service.context(state), "findings": findings})
        return {"draft": {**findings, "suitability": suitability}, "repair_count": 0}
    except Exception as error:
        return failed(error)


async def validate_traceability(state):
    try:
        draft = service.validate_draft(state)
        outcome = {"validated_draft": draft, "validation_error": None, "validation_details": {}}
    except (ValidationError, analysis.AnalysisError) as error:
        outcome = {"validation_error": str(error) if isinstance(error, analysis.AnalysisError) else "F6_SCHEMA_INVALID"}
        outcome["validation_details"] = error.details if isinstance(error, analysis.AnalysisError) else {
            "schema_errors": error.errors(include_input=False, include_url=False)}
        if state.get("repair_count", 0) >= 2:
            outcome.update(status="needs_review", error_code="F6_TRACEABILITY_NEEDS_REVIEW")
    await analysis.stage_write(state, "f6-validation", {"error": outcome["validation_error"],
        "details": outcome["validation_details"], "repairs": state.get("repair_count", 0)})
    return outcome


async def repair_assessment(state):
    count = state.get("repair_count", 0) + 1
    try:
        draft = await service.request(state, f"f6-repair-{count}", AssessmentDraft,
            "Repair the assessment using only supplied sources and criteria. Validation error: " + state["validation_error"]
            + ". Return all four finding lists and suitability. Remove unsupported claims or citations; "
            "do not turn absent evidence into a weakness. Never modify scores. Preserve valid findings. "
            "Use eligible_sections and validation_details to correct the rejected finding. Move admitted learning "
            "needs to actionable recommended_areas checks when they lack weakness/gap support; do not change "
            "citations to unrelated evidence. Honor required_position when supplied. "
            "Where weaknesses or gaps exist, recommended_areas must contain actionable verification steps "
            "with criterion-compatible citations. Other unsupported finding lists may be empty.",
            {**service.context(state), "previous_draft": state["draft"], "validation_details": state.get("validation_details", {})})
        return {"draft": draft, "repair_count": count}
    except Exception as error:
        return {**failed(error), "repair_count": count}


async def generate_report_data(state):
    return {"artifact": service.report(state)}


async def persist(state):
    artifact = state.get("artifact") or {"schema": service.REPORT_VERSION, "run_id": state["storage_run_id"],
        "status": state.get("status", "needs_review"), "error_code": state.get("error_code"),
        "validation_error": state.get("validation_error"),
        "synthetic": state["inputs"]["prepared"]["synthetic"], "publishable": False, "f6_ready": False,
        "provenance": {"f5_run_id": state["inputs"]["parent_run_id"], "repairs_used": state.get("repair_count", 0)},
        "warnings": ["F6_DRAFT_WITHHELD"]}
    # Invalid draft text is retained only in private stage checkpoints, not exposed as a report.
    await store.finish(state, artifact)
    return {"artifact": artifact}
