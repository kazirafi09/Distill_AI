"""Retrieve question bank chunks using the saved job description title."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.jd import JDConversation, JobDescription
from app.models.question_bank import QuestionBank
from app.services.jd.reference_index import embed_texts
from app.services.question_banks.indexing import (
    current_index_version,
    get_question_bank_collection,
)


def search_chunks(
    recruiter_id: UUID,
    job_title: str,
    banks: list[QuestionBank],
    limit: int,
) -> list[dict]:
    version = current_index_version()
    owned_banks = {str(bank.id): bank for bank in banks}

    filters = {
        "$and": [
            {"recruiter_id": str(recruiter_id)},
            {"bank_id": {"$in": list(owned_banks)}},
            {"index_version": version},
        ]
    }

    collection = get_question_bank_collection()
    available = collection.get(
        where=filters,
        include=[],
        limit=limit,
    )
    result_count = len(available["ids"])

    if not result_count:
        raise ValueError("QUESTION_BANK_INDEX_MISSING")

    result = collection.query(
        query_embeddings=embed_texts([job_title]),
        where=filters,
        n_results=result_count,
        include=["documents", "metadatas", "distances"],
    )

    matches = []
    seen_text = set()

    for chunk_id, text, metadata, distance in zip(
        result["ids"][0],
        result["documents"][0],
        result["metadatas"][0],
        result["distances"][0],
        strict=True,
    ):
        if not text or metadata is None or distance is None:
            raise ValueError("QUESTION_BANK_RESULT_INVALID")

        bank = owned_banks.get(metadata.get("bank_id"))
        if (
            bank is None
            or metadata.get("recruiter_id") != str(recruiter_id)
            or metadata.get("index_version") != version
            or metadata.get("file_sha256") != bank.file_sha256
        ):
            raise ValueError("QUESTION_BANK_SCOPE_INVALID")

        source = next(
            (
                source
                for source in bank.sources
                if source["id"] == metadata.get("source_id")
            ),
            None,
        )
        start = metadata.get("start")
        end = metadata.get("end")

        if (
            source is None
            or not isinstance(start, int)
            or not isinstance(end, int)
            or not source["start"] <= start < end <= source["end"]
            or (bank.extracted_text or "")[start:end] != text
        ):
            raise ValueError("QUESTION_BANK_SOURCE_INVALID")

        if distance > get_settings().rag_max_distance:
            continue

        normalized_text = " ".join(text.split()).casefold()
        if normalized_text in seen_text:
            continue
        seen_text.add(normalized_text)

        matches.append({
            "chunk_id": chunk_id,
            "bank_id": str(bank.id),
            "filename": bank.filename,
            "index_version": version,
            "source_id": source["id"],
            "page": source.get("page"),
            "method": source["method"],
            "start": start,
            "end": end,
            "text": text,
            "distance": float(distance),
        })

    return matches


async def retrieve_interview_questions(
    recruiter_id: UUID,
    job_description_id: UUID,
    limit: int = 6,
) -> dict:
    if not 1 <= limit <= 10:
        raise ValueError("QUESTION_LIMIT_INVALID")

    async with SessionFactory() as db:
        job = await db.scalar(
            select(JobDescription)
            .join(
                JDConversation,
                JobDescription.conversation_id == JDConversation.id,
            )
            .where(
                JobDescription.id == job_description_id,
                JDConversation.recruiter_id == recruiter_id,
            )
        )
        if job is None:
            raise HTTPException(404, "Job description not found.")

        title = job.title.strip()
        if not title:
            raise ValueError("JOB_TITLE_MISSING")

        banks = list(await db.scalars(
            select(QuestionBank).where(
                QuestionBank.recruiter_id == recruiter_id,
                QuestionBank.status == "ready",
                QuestionBank.is_archived.is_(False),
                QuestionBank.index_version == current_index_version(),
                QuestionBank.chunk_count > 0,
            )
        ))

    # Close the database transaction before embedding and Chroma calls.
    context = {
        "job_description_id": str(job_description_id),
        "job_title": title,
    }

    if not banks:
        return {
            **context,
            "status": "no_ready_banks",
            "chunks": [],
        }

    chunks = await run_in_threadpool(
        search_chunks,
        recruiter_id,
        title,
        banks,
        limit,
    )

    return {
        **context,
        "status": "retrieved" if chunks else "no_relevant_matches",
        "chunks": chunks,
    }
