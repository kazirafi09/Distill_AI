"""Synthetic adapter for the existing shared parsing and assessment LangGraphs."""
from pathlib import Path
from typing import Awaitable, Callable
from uuid import UUID

from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services.cv.processing import ParsedSource, SourceAccess
from app.services.cv.scoring import RUBRIC, validate_scoring_profile
from app.services.wind_tunnel import storage
from app.services.wind_tunnel.sources import SyntheticAssessment, SyntheticDocument, SyntheticScope
from app.workflows.cv_parsing import build_source_parsing_graph
from app.workflows.cv_screening import build_source_assessment_graph


def build_synthetic_assessment_graph(saver, root: Path, document: SyntheticDocument,
                                     expected_scope: SyntheticScope, profile: dict,
                                     profile_version: int, parse_id: UUID, versions: dict,
                                     retrieve: Callable[[dict, ParsedSource], Awaitable[dict]],
                                     prepare_index: Callable[[ParsedSource], Awaitable[None]],
                                     *, rubric: dict = RUBRIC, record: SyntheticAssessment | None = None,
                                     persist: Callable[[SyntheticAssessment], Awaitable[None]] | None = None):
    """Return the existing graph with source-specific result/save callbacks.

Only source access, persistence and retrieval differ. Fixture providers replace
    index/retrieval during offline verification. Persistent adapters supply a
    loaded record and short-save callback. There is no second assessment loop.
"""
    document.path(root, expected_scope)
    profile = validate_scoring_profile(profile, profile_version)
    assessment = record or SyntheticAssessment(ParsedSource(parse_id, document.id, document.file_sha256, versions))
    parsed = assessment.parsed
    if (parsed.id != parse_id or parsed.file_version_id != document.id
            or parsed.file_sha256 != document.file_sha256 or parsed.versions != versions):
        raise ValueError("SYNTHETIC_RESULT_SOURCE_MISMATCH")
    access = SourceAccess(document.id, lambda: document.path(root, expected_scope),
                          lambda: document.verify(root, expected_scope))

    async def save():
        if persist is not None:
            await persist(assessment)

    async def parse_status(status):
        parsed.status = status
        await save()

    async def parse(state):
        if state["assessment_id"] != str(assessment.id):
            raise ValueError("ASSESSMENT_SCOPE_INVALID")
        await run_in_threadpool(access.verify)
        if parsed.status != "completed":
            assessment.status = "parsing"
            graph = build_source_parsing_graph(False, parsed, access, parse_status)
            await graph.ainvoke({"file_version_id": str(document.id)})
        if parsed.status != "completed":
            raise ValueError(parsed.error_code or "CV_PARSING_FAILED")
        assessment.warnings = list(parsed.warnings)
        return {}

    async def index(state):
        assessment.status = "indexing"
        await prepare_index(parsed)
        return {"reuse": False}  # Independent assessments never reuse matches.

    async def parsed_results():
        return [parsed]

    async def retrieve_scoped(criterion):
        result = await retrieve(criterion, parsed)
        for excerpt in result["evidence"]:
            source = next((s for s in parsed.sources if s["id"] == excerpt["source_id"]), None)
            start, end = excerpt["start"], excerpt["end"]
            if (str(parsed.id) != excerpt["parse_id"]
                    or str(parsed.file_version_id) != excerpt["file_version_id"]
                    or source is None or not source["start"] <= start < end <= source["end"]
                    or parsed.text[start:end] != excerpt["quote"]):
                raise ValueError("SYNTHETIC_EVIDENCE_SCOPE_INVALID")
        return result

    graph = build_source_assessment_graph(saver, assessment, profile, rubric,
                                          parse, index, parsed_results, retrieve_scoped, save)
    return graph, assessment


async def load_assessment_graph(run_id, assessment_id, owner_id, retrieve, prepare_index,
                                *, root=None, saver=False, session_factory=None, extraction=None, writer_token=None):
    context = await storage.load_assessment_context(run_id, assessment_id, owner_id,
        root=root, register_checkpoint=saver is not False and saver is not None,
        session_factory=session_factory, extraction=extraction, writer_token=writer_token)
    document, record = context.document, context.record
    graph, record = build_synthetic_assessment_graph(saver, root or get_settings().wind_tunnel_storage_path,
        document, document.scope, context.profile, context.profile_version, record.parsed.id,
        record.parsed.versions, retrieve, prepare_index, rubric=context.rubric, record=record, persist=context.persist)
    return graph, record, context.config
