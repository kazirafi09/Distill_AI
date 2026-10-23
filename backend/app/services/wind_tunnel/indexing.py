"""Run-scoped vector adapter, reusing ordinary CV chunking and embeddings.

Indexes are document/text based; retrieved evidence is rebound to the current
independent parse ID only after source/hash/offset checks. No candidate records.
"""
from copy import deepcopy
from contextlib import asynccontextmanager
from uuid import UUID, uuid5

import chromadb
from fastapi import HTTPException
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.services.cv.indexing import index_chunks
from app.services.jd.reference_index import embed_texts
from app.services.wind_tunnel import storage

INDEX_ADAPTER_VERSION = "synthetic-index-1"


def collection(versions):
    settings = get_settings()
    return chromadb.HttpClient(host=settings.chroma_host, port=settings.chroma_port).get_or_create_collection(
        name="wind_tunnel_evidence_v1_" + storage.digest(versions)[:16], embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}})


def scope_metadata(scope):
    return {"owner_id": str(scope.owner_id), "job_id": str(scope.job_id), "run_id": str(scope.run_id)}


def where_for(scope, entry):
    return {"$and": [{key: value} for key, value in {
        **scope_metadata(scope), "index_id": entry["index_id"], "file_version_id": entry["document_id"]}.items()]}


@asynccontextmanager
async def vector_write(scope, factory):
    # Session-level lock on a reserved connection, committed before embedding
    # requests. Cleanup waits for an in-flight upsert; a later writer rechecks
    # writable state after locking. No SQL transaction spans provider work.
    key = int.from_bytes(bytes.fromhex(storage.digest(["f11-vectors", str(scope.run_id)]))[:8], "big", signed=True)
    async with factory.kw["bind"].connect() as connection:
        try:
            await connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": key})
            await connection.commit()
            yield
        finally:
            # Physically close this reserved session even on cancellation, so a
            # session advisory lock cannot leak into the connection pool.
            await connection.invalidate()


class SyntheticIndex:
    def __init__(self, run_id, owner_id, token, *, root, session_factory):
        self.run_id, self.owner_id, self.token = run_id, owner_id, token
        self.root, self.factory = root, session_factory

    async def prepare(self, parsed):
        async with self.factory() as db:
            run = await storage.owned_analysis(db, self.run_id, self.owner_id, self.token)
            _, document, _ = await storage.owned_document(db, run.id, parsed.file_version_id, self.owner_id, root=self.root)
            if parsed.file_sha256 != document.file_sha256:
                raise ValueError("SYNTHETIC_INDEX_SOURCE_INVALID")
            versions = run.versions["processing"]["indexing"]
            key = storage.digest({"document_id": str(document.id), "file_sha256": document.file_sha256,
                "text": parsed.text, "sources": parsed.sources, "versions": versions, "adapter": INDEX_ADAPTER_VERSION})
            identifier = str(uuid5(run.id, "index:" + key))
            entries = deepcopy(run.resources.get("vectors", []))
            entry = next((e for e in entries if e["index_id"] == identifier), None)
            if entry is None:
                entry = {"index_id": identifier, "key": key, "document_id": str(document.id),
                         "versions": versions, "status": "indexing", "chunk_count": 0}
                entries.append(entry)
            # Register before any external write, including partially failed upserts.
            run.resources = {**run.resources, "vectors": entries}
            await db.commit()
        try:
            async with vector_write(document.scope, self.factory):
                async with self.factory() as db:
                    await storage.owned_analysis(db, self.run_id, self.owner_id, self.token)
                    await db.commit()
                target = await run_in_threadpool(collection, versions)
                found = await run_in_threadpool(target.get, where=where_for(document.scope, entry), include=[])
                if entry["status"] == "indexed" and len(found["ids"]) == entry["chunk_count"] > 0:
                    return
                count = await run_in_threadpool(index_chunks, UUID(identifier), parsed.text, parsed.sources,
                    {**scope_metadata(document.scope), "file_version_id": str(document.id),
                     "file_sha256": document.file_sha256, "processing_key": key}, versions, target=target)
        except HTTPException:
            raise
        except Exception:
            raise ValueError("SYNTHETIC_INDEX_UNAVAILABLE") from None
        async with self.factory() as db:
            run = await storage.owned_analysis(db, self.run_id, self.owner_id, self.token)
            run.resources = {**run.resources, "vectors": [
                {**e, "status": "indexed", "chunk_count": count} if e["index_id"] == identifier else e
                for e in run.resources["vectors"]]}
            await db.commit()

    async def retrieve(self, criterion, parsed):
        query = criterion["text"]
        if not query.strip() or len(query.encode()) > 8000:
            raise ValueError("INVALID_EVIDENCE_QUERY")
        async with self.factory() as db:
            run = await storage.owned_analysis(db, self.run_id, self.owner_id, self.token)
            _, document, _ = await storage.owned_document(db, run.id, parsed.file_version_id, self.owner_id, root=self.root)
            if parsed.file_sha256 != document.file_sha256:
                raise ValueError("SYNTHETIC_INDEX_SOURCE_INVALID")
            versions = run.versions["processing"]["indexing"]
            key = storage.digest({"document_id": str(document.id), "file_sha256": document.file_sha256,
                "text": parsed.text, "sources": parsed.sources, "versions": versions, "adapter": INDEX_ADAPTER_VERSION})
            entry = next((deepcopy(e) for e in run.resources.get("vectors", []) if e["key"] == key and e["status"] == "indexed"), None)
            if entry is None:
                raise ValueError("SYNTHETIC_INDEX_UNAVAILABLE")
            await db.commit()

        def search():
            return collection(versions).query(query_embeddings=embed_texts([query]),
                n_results=min(4, entry["chunk_count"]), where=where_for(document.scope, entry), include=["metadatas", "distances"])

        try:
            found = await run_in_threadpool(search)
        except Exception:
            raise ValueError("SYNTHETIC_INDEX_UNAVAILABLE") from None
        evidence = []
        for metadata, distance in zip(found["metadatas"][0], found["distances"][0], strict=True):
            expected = {**scope_metadata(document.scope), "index_id": entry["index_id"],
                        "file_version_id": str(document.id), "file_sha256": document.file_sha256, "processing_key": key}
            if any(metadata.get(k) != v for k, v in expected.items()):
                raise ValueError("SYNTHETIC_EVIDENCE_SCOPE_INVALID")
            source = next((s for s in parsed.sources if s["id"] == metadata["source_id"]), None)
            start, end = metadata["start"], metadata["end"]
            if not source or not source["start"] <= start < end <= source["end"]:
                raise ValueError("SYNTHETIC_EVIDENCE_SCOPE_INVALID")
            evidence.append({"file_version_id": str(document.id), "parse_id": str(parsed.id), "source_id": source["id"],
                "quote": parsed.text[start:end], "start": start, "end": end, "ocr_derived": source["method"] == "ocr", "distance": distance})
        return {"evidence": sorted(evidence, key=lambda e: e["distance"])[:8], "warnings": []}


async def remove_vectors(scope, entries, *, session_factory=None):
    """Pass to storage.cleanup_run; scoped deletion also removes partial indexes."""
    async with vector_write(scope, session_factory or SessionFactory):
        for entry in entries:
            if (str(uuid5(scope.run_id, "index:" + entry["key"])) != entry["index_id"]
                    or str(UUID(entry["document_id"])) != entry["document_id"]):
                raise ValueError("SYNTHETIC_VECTOR_CLEANUP_SCOPE_INVALID")
            target = await run_in_threadpool(collection, entry["versions"])
            await run_in_threadpool(target.delete, where=where_for(scope, entry))
