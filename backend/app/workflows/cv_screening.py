from typing import TypedDict
import asyncio
import logging
import traceback
from uuid import UUID
from langgraph.graph import START, END, StateGraph
from langgraph.errors import NodeCancelledError
from sqlalchemy import select
from app.db.session import SessionFactory
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.services.cv.indexing import digest, ensure_index
from app.services.cv.matching import match_criterion, validate_match, MatchValidationFailure, REPAIR_GUIDANCE
from app.services.cv.parsing import parse_cv
from app.services.cv.scoring import calculate_scores, rank_assessments
from app.services.cv.screening import (
    retrieve_through_mcp, inputs_for, owned_job, commit_screening_write,
    valid_aggregate_score,
)
from app.workflows.runtime import open_async_workflow_checkpointer


class CandidateState(TypedDict, total=False):
    assessment_id: str
    reuse: bool


def build_candidate_graph(saver, db, run, assessment):
    async def set_status(status):
        assessment.status = status
        await commit_screening_write(db, run.job_id)

    async def parse(state):
        if state["assessment_id"] != str(assessment.id):
            raise ValueError("ASSESSMENT_SCOPE_INVALID")
        await set_status("parsing")
        if not assessment.file_version_ids:
            raise ValueError("NO_DOWNLOADED_CV")
        if len(assessment.file_version_ids) > 5:
            raise ValueError("TOO_MANY_CV_DOCUMENTS")
        ids, warnings = [], []
        for file_id in assessment.file_version_ids:
            result = await parse_cv(UUID(file_id), run.recruiter_id)
            warnings.extend(result.get("warnings", []))
            if result["status"] != "completed":
                assessment.warnings = list(dict.fromkeys(warnings + [result.get("error_code") or "CV_PARSE_INCOMPLETE"]))
                await commit_screening_write(db, run.job_id)
                raise ValueError("CV_TEXT_NEEDS_REVIEW" if result["status"] == "needs_review" else "CV_PARSING_FAILED")
            if result["versions"] != run.inputs["versions"]["parsing"]:
                raise ValueError("SCREENING_INPUTS_CHANGED")
            ids.append(result["id"])
        assessment.parse_result_ids = ids
        assessment.warnings = list(dict.fromkeys(warnings))
        assessment.processing_key = digest({"files": assessment.file_version_ids, "parse_ids": ids,
            "profile": run.inputs["profile"], "profile_version": run.inputs["profile_version"],
            "versions": run.inputs["versions"], "rubric": run.inputs["rubric"]})
        await commit_screening_write(db, run.job_id)
        return {}

    async def parsed_results():
        return [await db.get(CVParseResult, UUID(v)) for v in assessment.parse_result_ids]

    async def index(state):
        await set_status("indexing")
        indexed = []
        for parsed in await parsed_results():
            result = await ensure_index(db, parsed, run.recruiter_id, run.job_id, assessment.application_id,
                run.inputs["versions"]["indexing"])
            indexed.append(str(result.id))
        assessment.index_ids = indexed
        await commit_screening_write(db, run.job_id)
        previous = await db.scalar(select(CVAssessment).where(CVAssessment.id != assessment.id,
            CVAssessment.application_id == assessment.application_id, CVAssessment.processing_key == assessment.processing_key,
            CVAssessment.status == "completed").order_by(CVAssessment.updated_at.desc()).limit(1))
        if previous:
            originals = await parsed_results()
            assessment.matches = [validate_match(m, next(c for c in run.inputs["profile"]["criteria"] if c["id"] == m["requirement_id"]), originals)
                for m in previous.matches]
            assessment.reused_from = str(previous.id)
            assessment.warnings = list(dict.fromkeys(assessment.warnings + previous.warnings))
            await commit_screening_write(db, run.job_id)
        return {"reuse": bool(previous)}

    async def match(state):
        await set_status("matching")
        parsed = await parsed_results()
        for criterion in run.inputs["profile"]["criteria"]:
            existing = next((m for m in assessment.matches if m["requirement_id"] == criterion["id"]), None)
            if existing:
                validate_match(existing, criterion, parsed)
                continue
            retrieved = await retrieve_through_mcp(run.job_id, assessment.application_id, criterion["text"], run.recruiter_id, assessment.id)
            decision, warnings = await match_criterion(criterion, parsed, retrieved, run.inputs["profile"])
            assessment.matches = [*assessment.matches, decision]
            assessment.warnings = list(dict.fromkeys(assessment.warnings + warnings + retrieved.get("warnings", [])))
            await commit_screening_write(db, run.job_id)
        return {}

    async def score(state):
        await set_status("scoring")
        parsed = await parsed_results()
        criteria = run.inputs["profile"]["criteria"]
        if len(assessment.matches) != len(criteria):
            raise ValueError("MATCH_COVERAGE_INVALID")
        validated = [validate_match(m, next(c for c in criteria if c["id"] == m["requirement_id"]), parsed) for m in assessment.matches]
        assessment.matches = validated
        assessment.score = calculate_scores(run.inputs["profile"], validated, run.inputs["rubric"])
        assessment.error_code = None
        assessment.warnings = [warning for warning in assessment.warnings if warning not in REPAIR_GUIDANCE]
        await set_status("completed")
        return {}

    graph = StateGraph(CandidateState)
    for name, node in (("parse_cv_subworkflow", parse), ("index_cv", index), ("match_requirements", match), ("score_candidate", score)):
        graph.add_node(name, node)
    graph.add_edge(START, "parse_cv_subworkflow")
    graph.add_edge("parse_cv_subworkflow", "index_cv")
    graph.add_conditional_edges("index_cv", lambda s: "score_candidate" if s["reuse"] else "match_requirements")
    graph.add_edge("match_requirements", "score_candidate")
    graph.add_edge("score_candidate", END)
    return graph.compile(checkpointer=saver)


async def process_candidate(run_id, assessment_id):
    async with SessionFactory() as db:
        run, assessment = await db.get(ScreeningRun, run_id), await db.get(CVAssessment, assessment_id)
        job_id = run.job_id
        if assessment.status == "completed" or run.status == "outdated":
            return
        try:
            async with open_async_workflow_checkpointer() as saver:
                graph = build_candidate_graph(saver, db, run, assessment)
                config = {"configurable": {"thread_id": f"cv-assess:{assessment.id}:{assessment.attempt}"}, "recursion_limit": 20}
                snapshot = await graph.aget_state(config)
                await graph.ainvoke(None if snapshot.next else {"assessment_id": str(assessment.id)}, config)
        except NodeCancelledError:
            raise asyncio.CancelledError from None
        except Exception as exc:
            logging.getLogger("distill_ai.cv_worker").warning("Assessment failure type=%s; frames=%s", type(exc).__name__,
                [(frame.name, frame.lineno) for frame in traceback.extract_tb(exc.__traceback__)[-5:]])
            await db.rollback()
            assessment = await db.get(CVAssessment, assessment_id)
            known = {"NO_DOWNLOADED_CV", "TOO_MANY_CV_DOCUMENTS", "CV_TEXT_NEEDS_REVIEW", "CV_PARSING_FAILED",
                "SCREENING_INPUTS_CHANGED", "INDEXING_FAILED", "MATCH_INPUT_TOO_LARGE", "MATCH_OUTPUT_TOO_LARGE",
                "MATCH_SCHEMA_OR_EVIDENCE_INVALID", "MCP_EVIDENCE_RETRIEVAL_FAILED", "MCP_EVIDENCE_SCOPE_INVALID",
                "MODEL_API_KEY_MISSING", "MODEL_AUTH_FAILED", "MODEL_NOT_AVAILABLE", "MODEL_RATE_LIMITED",
                "MODEL_REQUEST_INVALID", "MODEL_RESPONSE_BLOCKED", "MODEL_UNAVAILABLE"}
            code = str(exc) if isinstance(exc, ValueError) and str(exc) in known else "ASSESSMENT_FAILED"
            if isinstance(exc, TimeoutError):
                code = "MATCH_TIMEOUT"
            assessment.status = "needs_review" if code in {"NO_DOWNLOADED_CV", "TOO_MANY_CV_DOCUMENTS", "CV_TEXT_NEEDS_REVIEW", "MATCH_INPUT_TOO_LARGE", "MATCH_OUTPUT_TOO_LARGE"} else "failed"
            assessment.error_code = code
            if isinstance(exc, MatchValidationFailure):
                assessment.warnings = list(dict.fromkeys(assessment.warnings + [exc.validation_code]))
            await commit_screening_write(db, job_id)


class BatchState(TypedDict, total=False):
    run_id: str
    more: bool


def build_screening_graph(saver):
    async def candidate(state):
        async with SessionFactory() as db:
            run = await db.get(ScreeningRun, UUID(state["run_id"]))
            if run.status == "outdated":
                return {"more": False}
            job = await owned_job(db, run.job_id, run.recruiter_id)
            if digest(await inputs_for(db, job, run.recruiter_id)) != run.input_key:
                run.status = "outdated"
                await commit_screening_write(db, run.job_id)
                return {"more": False}
            assessment_id = await db.scalar(select(CVAssessment.id).where(CVAssessment.run_id == run.id,
                CVAssessment.status.not_in(["completed", "failed", "needs_review"])).order_by(CVAssessment.application_id).limit(1))
        if assessment_id:
            await process_candidate(run.id, assessment_id)
        async with SessionFactory() as db:
            remaining = await db.scalar(select(CVAssessment.id).where(CVAssessment.run_id == run.id,
                CVAssessment.status.not_in(["completed", "failed", "needs_review"])).limit(1))
        return {"more": remaining is not None}

    async def rank(state):
        async with SessionFactory() as db:
            run = await db.get(ScreeningRun, UUID(state["run_id"]))
            if run.status == "outdated":
                return {}
            job = await owned_job(db, run.job_id, run.recruiter_id)
            if digest(await inputs_for(db, job, run.recruiter_id)) != run.input_key:
                run.status = "outdated"
                await commit_screening_write(db, run.job_id)
                return {}
            assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id == run.id)))
            ordered = rank_assessments([{"id": str(a.id), "application_id": str(a.application_id), "score": a.score}
                for a in assessments if a.status == "completed" and valid_aggregate_score(a.score)])
            by_id = {r["id"]: r["rank"] for r in ordered}
            for item in assessments:
                if str(item.id) in by_id:
                    item.score = {**item.score, "rank": by_id[str(item.id)]}
            run.status = "completed" if all(a.status == "completed" for a in assessments) else "completed_with_issues"
            await commit_screening_write(db, run.job_id)
        return {}

    graph = StateGraph(BatchState)
    graph.add_node("process_one_application", candidate)
    graph.add_node("rank_completed", rank)
    graph.add_edge(START, "process_one_application")
    graph.add_conditional_edges("process_one_application", lambda s: "process_one_application" if s["more"] else "rank_completed")
    graph.add_edge("rank_completed", END)
    return graph.compile(checkpointer=saver)
