"""Index question bank chunks using existing Ollama embeddings."""

from hashlib import sha256
from functools import lru_cache


from app.core.config import get_settings
from app.models.question_bank import QuestionBank
from app.services.jd.reference_index import embed_texts


CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MAX_CHUNKS = 1000
BATCH_SIZE = 8


def current_index_version() -> str:
    settings = get_settings()
    configuration = (
        f"question-banks-v1:{settings.embedding_model}:"
        f"{CHUNK_SIZE}:{CHUNK_OVERLAP}"
    )
    return sha256(configuration.encode("utf-8")).hexdigest()[:16]


@lru_cache
def get_question_bank_collection():
    import chromadb

    settings = get_settings()
    client = chromadb.HttpClient(
        host=settings.chroma_host,
        port=settings.chroma_port,
    )
    return client.get_or_create_collection(
        name=f"question_banks_{current_index_version()}",
        embedding_function=None,
        configuration={"hnsw": {"space": "cosine"}},
    )


def build_chunks(bank: QuestionBank) -> list[dict]:
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    text = bank.extracted_text or ""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,
    )
    chunks = []

    for source in bank.sources:
        source_start = source["start"]
        source_end = source["end"]

        if not 0 <= source_start <= source_end <= len(text):
            raise ValueError("SOURCE_OFFSETS_INVALID")

        content = text[source_start:source_end]

        for part in splitter.create_documents([content]):
            start = source_start + part.metadata["start_index"]
            end = start + len(part.page_content)

            if (
                not source_start <= start < end <= source_end
                or text[start:end] != part.page_content
            ):
                raise ValueError("CHUNK_OFFSETS_INVALID")

            chunks.append({
                "text": part.page_content,
                "source_id": source["id"],
                "page": source.get("page") or 0,
                "method": source["method"],
                "start": start,
                "end": end,
            })

            if len(chunks) > MAX_CHUNKS:
                raise ValueError("TOO_MANY_CHUNKS")

    if not chunks:
        raise ValueError("NO_INDEXABLE_TEXT")

    return chunks


def index_question_bank(bank: QuestionBank) -> tuple[int, str]:
    chunks = build_chunks(bank)
    version = current_index_version()
    collection = get_question_bank_collection()

    # Retrying replaces this bank's chunks in the current collection.
    collection.delete(where={"bank_id": str(bank.id)})

    for offset in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[offset:offset + BATCH_SIZE]
        documents = [chunk["text"] for chunk in batch]
        embeddings = embed_texts(documents)

        metadata = [
            {
                "bank_id": str(bank.id),
                "recruiter_id": str(bank.recruiter_id),
                "filename": bank.filename,
                "file_sha256": bank.file_sha256,
                "index_version": version,
                "chunk_index": offset + position,
                "source_id": chunk["source_id"],
                "page": chunk["page"],
                "method": chunk["method"],
                "start": chunk["start"],
                "end": chunk["end"],
            }
            for position, chunk in enumerate(batch)
        ]

        collection.upsert(
            ids=[
                f"{bank.id}:{offset + position}"
                for position in range(len(batch))
            ],
            documents=documents,
            embeddings=embeddings,
            metadatas=metadata,
        )

    return len(chunks), version
