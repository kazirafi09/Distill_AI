import json
import logging
from functools import lru_cache
from hashlib import sha256
from threading import Lock
from urllib.request import Request, urlopen

import chromadb
from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.core.config import get_settings


logger = logging.getLogger("distill_ai.jd_references")

# Limit embedding work to one request at a time on the laptop.
_embedding_lock = Lock()

# Serialize indexing within this backend process.
_index_lock = Lock()


@lru_cache
def get_reference_collection():
    settings = get_settings()

    client = chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
    )

    model_hash = sha256(
        settings.embedding_model.encode("utf-8")
    ).hexdigest()[:12]

    return client.get_or_create_collection(
        name=f"jd_references_v1_{model_hash}",
        embedding_function=None,
        configuration={
            "hnsw": {
                "space": "cosine",
            },
        },
    )


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []

    settings = get_settings()

    payload = json.dumps({
        "model": settings.embedding_model,
        "input": texts,
        "truncate": False,
    }).encode("utf-8")

    request = Request(
        url=f"{settings.ollama_base_url.rstrip('/')}/api/embed",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with _embedding_lock:
        with urlopen(
            request,
            timeout=settings.embedding_timeout_seconds,
        ) as response:
            result = json.load(response)

    embeddings = result.get("embeddings")

    if (
        not isinstance(embeddings, list)
        or len(embeddings) != len(texts)
        or any(
            not isinstance(vector, list) or not vector
            for vector in embeddings
        )
    ):
        raise ValueError("Ollama returned invalid embeddings.")

    return embeddings


def index_reference_document(
    reference_id: str,
    recruiter_id: str,
    filename: str,
    document_type: str,
    extracted_text: str,
) -> int:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200,
    )

    chunks = [
        chunk
        for chunk in splitter.split_text(extracted_text)
        if chunk.strip()
    ]

    if not chunks:
        raise ValueError("The reference contains no indexable text.")

    with _index_lock:
        collection = get_reference_collection()

        # A retry replaces this reference's chunks.
        # Retrieval will also check PostgreSQL's indexed status.
        collection.delete(
            where={"reference_id": reference_id},
        )

        batch_size = 8

        for start in range(0, len(chunks), batch_size):
            batch = chunks[start:start + batch_size]
            embeddings = embed_texts(batch)

            collection.upsert(
                ids=[
                    f"{reference_id}:{start + position}"
                    for position in range(len(batch))
                ],
                documents=batch,
                embeddings=embeddings,
                metadatas=[
                    {
                        "reference_id": reference_id,
                        "recruiter_id": recruiter_id,
                        "filename": filename,
                        "document_type": document_type,
                        "chunk_index": start + position,
                    }
                    for position in range(len(batch))
                ],
            )

    logger.info(
        "Reference indexed | reference_id=%s | chunk_count=%s",
        reference_id,
        len(chunks),
    )

    return len(chunks)