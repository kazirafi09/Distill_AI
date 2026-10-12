import json
import logging
from uuid import UUID

import psycopg
from psycopg.conninfo import make_conninfo

from app.core.config import get_settings
from app.services.jd.reference_index import (
    embed_texts,
    get_reference_collection,
)
from app.workflows.jd.state import ReferenceSource


logger = logging.getLogger("distill_ai.jd_references")


def retrieve_reference_context(
    recruiter_id: UUID,
    query: str,
) -> tuple[str, list[ReferenceSource], str]:
    settings = get_settings()

    connection_info = make_conninfo(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password.get_secret_value(),
        connect_timeout=5,
    )

    # Shared row locks prevent an indexed reference being rebuilt
    # while this retrieval reads its chunks.
    with psycopg.connect(connection_info) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, document_type
                FROM jd_references
                WHERE recruiter_id = %s
                  AND index_status = 'indexed'
                ORDER BY id
                FOR SHARE
                """,
                (recruiter_id,),
            )

            rows = cursor.fetchall()

        if not rows:
            return "", [], "no_indexed_references"

        eligible = {
            "prior_jd": [],
            "company_policy": [],
        }

        for reference_id, document_type in rows:
            eligible[document_type].append(str(reference_id))

        collection = get_reference_collection()

        # Bound the embedding input independently of the full
        # requirements passed to JD generation.
        query_embedding = embed_texts([query[:12000]])[0]

        sources: list[ReferenceSource] = []
        excerpts: list[dict[str, object]] = []

        for document_type, top_k in (
            ("prior_jd", 3),
            ("company_policy", 2),
        ):
            reference_ids = eligible[document_type]

            if not reference_ids:
                continue

            where = {
                "$and": [
                    {"recruiter_id": str(recruiter_id)},
                    {"reference_id": {"$in": reference_ids}},
                    {"document_type": document_type},
                ],
            }

            # Fetch only enough IDs to choose a valid result count.
            available = collection.get(
                where=where,
                limit=top_k,
                include=[],
            )

            available_count = len(available["ids"])

            if available_count == 0:
                raise RuntimeError(
                    "Indexed references have no chunks in this Chroma "
                    "collection. Re-index the reference documents."
                )

            result = collection.query(
                query_embeddings=[query_embedding],
                where=where,
                n_results=available_count,
                include=["documents", "metadatas", "distances"],
            )

            documents = (result.get("documents") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]

            for document, metadata, distance in zip(
                documents,
                metadatas,
                distances,
                strict=True,
            ):
                if (
                    not document
                    or metadata is None
                    or distance is None
                    or distance > settings.rag_max_distance
                ):
                    continue

                reference_id = str(metadata["reference_id"])

                # Check returned metadata as well as the query filter.
                if (
                    metadata.get("recruiter_id") != str(recruiter_id)
                    or reference_id not in reference_ids
                    or metadata.get("document_type") != document_type
                ):
                    raise RuntimeError(
                        "Unexpected reference ownership metadata."
                    )

                source: ReferenceSource = {
                    "reference_id": reference_id,
                    "filename": str(metadata["filename"]),
                    "document_type": document_type,
                    "chunk_index": int(metadata["chunk_index"]),
                    "distance": float(distance),
                }

                excerpt = {
                    **source,
                    "text": document,
                }

                # Limit reference context sent to the generation model.
                candidate = json.dumps(
                    [*excerpts, excerpt],
                    ensure_ascii=False,
                )

                if len(candidate) > 8000:
                    continue

                excerpts.append(excerpt)
                sources.append(source)

    logger.info(
        "Reference retrieval complete | source_count=%s",
        len(sources),
    )

    if not sources:
        return "", [], "no_relevant_matches"

    return (
        json.dumps(excerpts, ensure_ascii=False),
        sources,
        "retrieved",
    )