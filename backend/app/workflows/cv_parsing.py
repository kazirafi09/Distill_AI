from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.services.cv_llm import extract_profile, check_input_size
from app.services.cv_profile_schema import identity_warnings, validate_profile
from app.services.cv_text import finalize_text, isolated_extract


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
    """No MCP calls: graph nodes call shared extraction/OCR/Ollama directly."""
    settings = get_settings()

    async def status(value):
        result.status = value
        await db.commit()

    async def load(state):
        # Ownership and exact-version loading occur in the shared entry service.
        if state["file_version_id"] != str(file_version.id):
            raise ValueError("CV_VERSION_MISMATCH")
        await status("verifying")
        return {}

    async def verify(state):
        from app.services.cv_parsing import verify_integrity
        await run_in_threadpool(verify_integrity, file_version)
        return {}

    async def extract(state):
        if result.text is not None:
            return {"saved_text": True}
        from app.services.cv_parsing import stored_path
        await status("extracting")
        extracted = await run_in_threadpool(isolated_extract, stored_path(file_version), "native", settings.cv_ocr_language,
                                            settings.cv_ocr_timeout_seconds, settings.cv_document_timeout_seconds, None, file_version.file_sha256)
        return {"extracted": extracted, "saved_text": False}

    async def assess(state):
        return {"ocr_needed": not state.get("saved_text") and any(s.get("ocr_needed") for s in state["extracted"]["sources"])}

    async def ocr(state):
        from app.services.cv_parsing import stored_path
        await status("ocr")
        from app.services.document_extraction import DocumentExtractionError
        try:
            extracted = await run_in_threadpool(isolated_extract, stored_path(file_version), "ocr", settings.cv_ocr_language,
                                                settings.cv_ocr_timeout_seconds, settings.cv_document_timeout_seconds, state["extracted"], file_version.file_sha256)
        except DocumentExtractionError as exc:
            if exc.code == "FILE_HASH_MISMATCH":
                raise
            # Retain native text and mark incomplete instead of losing all text
            # when the OCR worker exceeds the document deadline.
            extracted = state["extracted"]
            extracted["warnings"] = extracted["warnings"] + [exc.code]
            for source in extracted["sources"]:
                if source.get("ocr_needed"):
                    source["ocr_failed"] = True
        return {"extracted": extracted}

    async def validate_text(state):
        if not state.get("saved_text"):
            document = finalize_text(state["extracted"])
            result.text, result.sources = document["text"], document["sources"]
            result.warnings, result.extraction_method = document["warnings"], document["extraction_method"]
            usable = document["usable"]
        else:
            from app.services.cv_text import readable
            usable = any(readable(result.text[s["start"]:s["end"]]) for s in result.sources) and not any(s.get("ocr_failed") for s in result.sources)
        result.status = "text_saved" if usable else "needs_review"
        result.error_code = None if usable else "TEXT_UNUSABLE_OR_INCOMPLETE"
        await db.commit()  # Saved BEFORE any LLM call, including oversized-input check.
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
        result.warnings = list(dict.fromkeys(result.warnings + state.get("profile_warnings", []) + identity_warnings(result.profile, candidate.full_name, candidate.email)))
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
    for source, target in [("load_version", "verify_integrity"), ("verify_integrity", "extract_text"),
                           ("extract_text", "assess_quality"), ("local_ocr", "validate_text"),
                           ("qwen_extract", "validate_profile"), ("validate_profile", "persist_profile"), ("persist_profile", END)]:
        graph.add_edge(source, target)
    graph.add_conditional_edges("assess_quality", lambda s: "local_ocr" if s["ocr_needed"] else "validate_text")
    graph.add_conditional_edges("validate_text", lambda s: "qwen_extract" if s["usable"] else END)
    return graph.compile(checkpointer=checkpointer)
