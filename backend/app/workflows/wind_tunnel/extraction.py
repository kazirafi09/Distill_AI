"""Invoke the shared extraction-only subgraph and reconcile its durable cache."""
from copy import deepcopy
from time import perf_counter
from uuid import uuid4

from app.services.cv.processing import ParsedSource, SourceAccess
from app.services.wind_tunnel import storage
from app.services.wind_tunnel.extraction import extraction_config, validate_extraction
from app.workflows.cv_parsing import build_source_parsing_graph


async def ensure_extraction(run_id, document_id, owner_id, token, *, root, session_factory):
    async with session_factory() as db:
        run = await storage.owned_analysis(db, run_id, owner_id, token)
        _, document, _ = await storage.owned_document(db, run_id, document_id, owner_id, root=root)
        versions = run.versions["processing"]["parsing"]
        entry = deepcopy(run.resources.get("extractions", {}).get(str(document_id)))
        await db.commit()
    if entry is not None and entry.get("status") == "completed":
        validate_extraction(entry, document, versions)
        return entry
    # Failed/incomplete entries are not successful cache inputs.
    record = ParsedSource(uuid4(), document.id, document.file_sha256, versions)
    async def status(value):
        record.status = value

    graph = build_source_parsing_graph(False, record,
        SourceAccess(document.id, lambda: document.path(root, document.scope), lambda: document.verify(root, document.scope)),
        status, extraction_only=True)
    started = perf_counter()
    try:
        await graph.ainvoke({"file_version_id": str(document.id)})
        body = {key: getattr(record, key) for key in ("text", "sources", "warnings", "extraction_method")}
        entry = {**body, "document_id": str(document.id), "file_sha256": document.file_sha256,
                 "config": extraction_config(versions), "status": "completed" if record.status == "text_saved" else "failed",
                 "error_code": record.error_code, "content_digest": storage.digest(body)}
    except (ValueError, OSError, TimeoutError):
        entry = {"document_id": str(document.id), "status": "failed", "error_code": "SYNTHETIC_EXTRACTION_FAILED"}
    entry["duration_seconds"] = perf_counter() - started
    async with session_factory() as db:
        run = await storage.owned_analysis(db, run_id, owner_id, token)
        run.resources = {**run.resources, "extractions": {**run.resources.get("extractions", {}), str(document_id): entry}}
        await db.commit()
    if entry["status"] != "completed":
        raise ValueError("SYNTHETIC_EXTRACTION_FAILED")
    return entry
