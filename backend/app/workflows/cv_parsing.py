from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from starlette.concurrency import run_in_threadpool

from app.services.cv.llm import extract_profile, check_input_size
from app.core.config import get_settings
from app.services.cv.processing import SourceAccess
from app.services.cv.profile_schema import identity_warnings, validate_profile
from app.services.cv.text import finalize_text, isolated_extract


class ParsingState(TypedDict, total=False):
    file_version_id: str
    extracted: dict
    saved_text: bool
    ocr_needed: bool
    usable: bool
    profile: dict
    evidence: list
    profile_warnings: list


def build_cv_parsing_graph(checkpointer, db, result, file_version, candidate):
    async def status(value):
        result.status = value
        await db.commit()

    from app.services.cv.parsing import stored_path, verify_integrity
    return build_source_parsing_graph(
        checkpointer, result,
        SourceAccess(file_version.id, lambda: stored_path(file_version), lambda: verify_integrity(file_version)),
        status, identity=(candidate.full_name, candidate.email),
    )


def build_source_parsing_graph(checkpointer, result, source: SourceAccess, status, *, identity=None, extraction_only=False):
    """Existing parsing nodes shared by ordinary and synthetic storage adapters.

The ordinary entry service still authorizes before cache reads or graph startup.
Source access/status callbacks preserve each adapter's integrity and save rules.
"""
    settings = get_settings()

    async def load(state):
        # Ownership and exact-version loading occur in the shared entry service.
        if state["file_version_id"] != str(source.id):
            raise ValueError("CV_VERSION_MISMATCH")
        await status("verifying")
        return {}

    async def verify(state):
        await run_in_threadpool(source.verify)
        return {}

    async def extract(state):
        if result.text is not None:
            return {"saved_text": True}
        await status("extracting")
        extracted = await run_in_threadpool(isolated_extract, source.path(), "native", settings.cv_ocr_language,
                                            settings.cv_ocr_timeout_seconds, settings.cv_document_timeout_seconds,
                                            None, result.file_sha256)
        return {"extracted": extracted, "saved_text": False}

    async def assess(state):
        return {"ocr_needed": not state.get("saved_text") and any(s.get("ocr_needed") for s in state["extracted"]["sources"])}

    async def ocr(state):
        await status("ocr")
        from app.services.documents.extraction import DocumentExtractionError
        try:
            extracted = await run_in_threadpool(isolated_extract, source.path(), "ocr", settings.cv_ocr_language,
                                                settings.cv_ocr_timeout_seconds, settings.cv_document_timeout_seconds,
                                                state["extracted"], result.file_sha256)
        except DocumentExtractionError as exc:
            if exc.code == "FILE_HASH_MISMATCH":
                raise
            extracted = state["extracted"]
            extracted["warnings"] = extracted["warnings"] + [exc.code]
            for segment in extracted["sources"]:
                if segment.get("ocr_needed"):
                    segment["ocr_failed"] = True
        return {"extracted": extracted}

    async def validate_text(state):
        if not state.get("saved_text"):
            document = finalize_text(state["extracted"])
            result.text, result.sources = document["text"], document["sources"]
            result.warnings, result.extraction_method = document["warnings"], document["extraction_method"]
            usable = document["usable"]
        else:
            from app.services.cv.text import readable
            usable = any(readable(result.text[s["start"]:s["end"]]) for s in result.sources) and not any(s.get("ocr_failed") for s in result.sources)
        result.status = "text_saved" if usable else "needs_review"
        result.error_code = None if usable else "TEXT_UNUSABLE_OR_INCOMPLETE"
        await status(result.status)  # Ordinary adapter commits text BEFORE model calls.
        if usable:
            check_input_size(result.text, result.sources)
        return {"usable": usable}

    async def parse(state):
        await status("parsing")
        if result.versions["model_digest"] == "unavailable":
            raise ValueError("MODEL_UNAVAILABLE")
        import httpx
        warnings = []
        try:
            profile, evidence = await extract_profile(result.text, result.sources, warnings=warnings)
        except (TimeoutError, httpx.TimeoutException):
            raise ValueError("MODEL_TIMEOUT") from None
        return {"profile": profile, "evidence": evidence, "profile_warnings": warnings}

    async def validate(state):
        await status("validating")
        profile, evidence = validate_profile(state["profile"], result.text, result.sources)
        return {"profile": profile, "evidence": evidence}

    async def persist(state):
        result.profile, result.evidence = state["profile"], state["evidence"]
        identity_notes = identity_warnings(result.profile, *identity) if identity is not None else []
        result.warnings = list(dict.fromkeys(result.warnings + state.get("profile_warnings", []) + identity_notes))
        result.error_code = None
        await status("completed")
        return {}

    graph = StateGraph(ParsingState)
    nodes = [("load_version", load), ("verify_integrity", verify), ("extract_text", extract),
             ("assess_quality", assess), ("local_ocr", ocr), ("validate_text", validate_text),
             ("qwen_extract", parse), ("validate_profile", validate), ("persist_profile", persist)]
    for name, node in nodes:
        graph.add_node(name, node)
    graph.add_edge(START, "load_version")
    for source_node, target in [("load_version", "verify_integrity"), ("verify_integrity", "extract_text"),
                           ("extract_text", "assess_quality"), ("local_ocr", "validate_text"),
                           ("qwen_extract", "validate_profile"), ("validate_profile", "persist_profile"), ("persist_profile", END)]:
        graph.add_edge(source_node, target)
    graph.add_conditional_edges("assess_quality", lambda s: "local_ocr" if s["ocr_needed"] else "validate_text")
    graph.add_conditional_edges("validate_text", lambda s: "qwen_extract" if s["usable"] and not extraction_only else END)
    return graph.compile(checkpointer=checkpointer)
