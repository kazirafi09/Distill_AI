"""CV-only collection; existing JD-reference indexing is unchanged."""
import json
from hashlib import sha256
from uuid import UUID
import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from app.core.config import get_settings
from app.models.cv import CVDocument, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVIndex
from app.services.jd_reference_index import embed_texts

INDEX_VERSION = "cv-chunks-1"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def collection(versions):
    settings = get_settings()
    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port).get_or_create_collection(
        name="cv_evidence_v1_" + digest(versions)[:16], embedding_function=None, configuration={"hnsw": {"space": "cosine"}})


def index_chunks(index_id, text, sources, metadata, versions):
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200, add_start_index=True)
    chunks = []
    for source in sources:
        content = text[source["start"]:source["end"]]
        for part in splitter.create_documents([content]):
            start = source["start"] + part.metadata["start_index"]
            if start < source["start"] or text[start:start + len(part.page_content)] != part.page_content:
                raise ValueError("CHUNK_OFFSET_INVALID")
            chunks.append((part.page_content, {**metadata, "index_id": str(index_id), "source_id": source["id"],
                "method": source["method"], "page": source.get("page") or 0, "start": start, "end": start + len(part.page_content)}))
    if not chunks or len(chunks) > 1000:
        raise ValueError("INDEX_INPUT_TOO_LARGE")
    target = collection(versions)
    target.delete(where={"index_id": str(index_id)})
    for offset in range(0, len(chunks), 8):
        batch = chunks[offset:offset + 8]
        target.upsert(ids=[f"{index_id}:{offset + i}" for i in range(len(batch))],
            documents=[c[0] for c in batch], metadatas=[c[1] for c in batch], embeddings=embed_texts([c[0] for c in batch]))
    return len(chunks)


async def ensure_index(db, parsed, recruiter_id, job_id, application_id, versions):
    key = digest({"parse_key": parsed.processing_key, "text": digest(parsed.text), "file_hash": parsed.file_sha256, "versions": versions})
    indexed = await db.scalar(select(CVIndex).where(CVIndex.parse_result_id == parsed.id, CVIndex.processing_key == key))
    if indexed and indexed.status == "indexed":
        # A deleted/reset Chroma collection must be rebuilt, not treated as success.
        try:
            count = await run_in_threadpool(lambda: len(collection(versions).get(where={"index_id": str(indexed.id)}, include=[])["ids"]))
        except Exception:
            indexed.status, indexed.error_code = "failed", "INDEXING_FAILED"
            await db.commit()
            raise ValueError("INDEXING_FAILED") from None
        if count == indexed.chunk_count:
            return indexed
    if indexed is None:
        indexed = CVIndex(parse_result_id=parsed.id, processing_key=key, versions=versions)
        db.add(indexed)
    indexed.status, indexed.error_code = "indexing", None
    await db.commit()
    version = await db.get(CVFileVersion, parsed.file_version_id)
    try:
        indexed.chunk_count = await run_in_threadpool(index_chunks, indexed.id, parsed.text, parsed.sources,
            {"owner_id": str(recruiter_id), "job_id": str(job_id), "application_id": str(application_id),
             "document_id": str(version.cv_document_id), "file_version_id": str(version.id), "parse_id": str(parsed.id),
             "file_sha256": parsed.file_sha256, "processing_key": key}, versions)
        indexed.status = "indexed"
        await db.commit()
        return indexed
    except Exception:
        indexed.status, indexed.error_code = "failed", "INDEXING_FAILED"
        await db.commit()
        raise ValueError("INDEXING_FAILED") from None


async def search_evidence(job_id, application_id, query, recruiter_id, assessment_id=None):
    from app.db.session import SessionFactory
    from app.models.cv_screening import CVAssessment, ScreeningRun
    from app.services.cv_screening import owned_application
    if not query.strip() or len(query.encode()) > 8000:
        raise ValueError("INVALID_EVIDENCE_QUERY")
    async with SessionFactory() as db:
        await owned_application(db, job_id, application_id, recruiter_id)
        statement = select(CVAssessment).join(ScreeningRun).where(ScreeningRun.job_id == job_id,
            ScreeningRun.recruiter_id == recruiter_id, CVAssessment.application_id == application_id)
        if assessment_id:
            statement = statement.where(CVAssessment.id == assessment_id)
        assessment = await db.scalar(statement.order_by(ScreeningRun.created_at.desc()).limit(1))
        if not assessment:
            return {"job_id": str(job_id), "application_id": str(application_id), "evidence": [], "warnings": ["NO_ASSESSED_VERSIONS"]}
        output = []
        for index_id in assessment.index_ids:
            indexed = await db.get(CVIndex, UUID(index_id))
            if not indexed or indexed.status != "indexed" or str(indexed.parse_result_id) not in assessment.parse_result_ids:
                continue
            parsed = await db.get(CVParseResult, indexed.parse_result_id)
            if not parsed or str(parsed.file_version_id) not in assessment.file_version_ids or parsed.status != "completed":
                continue
            version = await db.get(CVFileVersion, parsed.file_version_id)
            document = await db.get(CVDocument, version.cv_document_id)
            if document.application_id != application_id or document.job_description_id != job_id:
                raise ValueError("EVIDENCE_SCOPE_INVALID")
            where = {"$and": [{"owner_id": str(recruiter_id)}, {"job_id": str(job_id)},
                {"application_id": str(application_id)}, {"index_id": str(indexed.id)}, {"file_version_id": str(parsed.file_version_id)}]}
            def retrieve():
                target = collection(indexed.versions)
                return target.query(query_embeddings=embed_texts([query]), n_results=min(4, indexed.chunk_count),
                    where=where, include=["metadatas", "distances"])
            found = await run_in_threadpool(retrieve)
            for metadata, distance in zip(found["metadatas"][0], found["distances"][0], strict=True):
                source = next((s for s in parsed.sources if s["id"] == metadata["source_id"]), None)
                start, end = metadata["start"], metadata["end"]
                if not source or not source["start"] <= start < end <= source["end"]:
                    raise ValueError("EVIDENCE_OFFSETS_INVALID")
                output.append({"file_version_id": str(parsed.file_version_id), "parse_id": str(parsed.id),
                    "source_id": source["id"], "quote": parsed.text[start:end], "start": start, "end": end,
                    "ocr_derived": source["method"] == "ocr", "distance": distance})
        return {"job_id": str(job_id), "application_id": str(application_id), "assessment_id": str(assessment.id),
                "evidence": sorted(output, key=lambda e: e["distance"])[:8], "warnings": []}
