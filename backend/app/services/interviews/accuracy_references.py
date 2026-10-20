"""Per-question reference retrieval using the existing document and bank indexes."""
import json
import logging
from uuid import UUID

from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.jd_reference import JDReference
from app.models.question_bank import QuestionBank
from app.services.jd.reference_retrieval import retrieve_reference_context
from app.services.question_banks.indexing import current_index_version
from app.services.question_banks.retrieval import search_chunks

logger = logging.getLogger("distill_ai.accuracy_references")
DOCUMENT_LIMITS = {"technical_reference": 3, "company_policy": 1}


async def snapshot_sources(db, recruiter_id):
    """Pin eligible source versions when a run is created, without copying full files."""
    documents = (await db.execute(select(JDReference.id, JDReference.file_sha256).where(
        JDReference.recruiter_id == recruiter_id, JDReference.index_status == "indexed",
        JDReference.document_type.in_(DOCUMENT_LIMITS)).order_by(JDReference.id))).all()
    banks = (await db.execute(select(QuestionBank.id, QuestionBank.file_sha256).where(
        QuestionBank.recruiter_id == recruiter_id, QuestionBank.status == "ready",
        QuestionBank.is_archived.is_(False), QuestionBank.chunk_count > 0,
        QuestionBank.index_version == current_index_version()).order_by(QuestionBank.id))).all()
    return {"documents": {str(id): version for id, version in documents},
        "question_banks": {str(id): version for id, version in banks},
        "embedding_model": get_settings().embedding_model, "policy": "qa-references-1"}


def question_query(state, group):
    """Search the question, follow-ups and mapped criteria, not an asserted answer."""
    ids = set(group["question_turn_ids"] + group.get("follow_up_turn_ids", []) + group.get("revisit_turn_ids", []))
    questions = [turn["text"] for turn in state["turns"] if turn["id"] in ids]
    criteria = [item["text"] for item in state["criteria"] if item["id"] in group["criterion_ids"]]
    return "\n".join([group["topic"], *questions, *criteria])[:4000]


async def retrieve(recruiter_id, query, sources):
    """Return immutable, bounded passages; retrieval failure is distinct from no matches."""
    references = []
    failures = []
    if sources["embedding_model"] != get_settings().embedding_model:
        return {"status": "failed", "references": [], "failed_sources": ["embedding_model_changed"]}
    if sources["documents"]:
        try:
            context, _, _ = await run_in_threadpool(retrieve_reference_context,
                UUID(str(recruiter_id)), query, document_limits=DOCUMENT_LIMITS, versions=sources["documents"])
            for chunk in json.loads(context or "[]"):
                references.append({"id": f"document:{chunk['reference_id']}:{chunk['chunk_index']}:{chunk['version']}",
                    "text": chunk["text"], "version": chunk["version"], "filename": chunk["filename"],
                    "document_type": chunk["document_type"], "location": f"Chunk {chunk['chunk_index'] + 1}",
                    "url": f"urn:distill:reference:{chunk['reference_id']}"})
        except Exception as error:
            logger.warning("Accuracy document retrieval failed | error_type=%s", type(error).__name__)
            failures.append("documents")
    if sources["question_banks"]:
        try:
            async with SessionFactory() as db:
                banks = list(await db.scalars(select(QuestionBank).where(
                    QuestionBank.recruiter_id == recruiter_id,
                    QuestionBank.id.in_([UUID(id) for id in sources["question_banks"]]),
                    QuestionBank.status == "ready", QuestionBank.is_archived.is_(False),
                    QuestionBank.index_version == current_index_version())))
            if {str(bank.id): bank.file_sha256 for bank in banks} != sources["question_banks"]:
                raise ValueError("QUESTION_BANK_SNAPSHOT_UNAVAILABLE")
            chunks = await run_in_threadpool(search_chunks, recruiter_id, query, banks, 3)
            for chunk in chunks:
                version = sources["question_banks"][chunk["bank_id"]]
                references.append({"id": f"bank:{chunk['chunk_id']}:{version}", "text": chunk["text"],
                    "version": version, "filename": chunk["filename"], "document_type": "question_bank",
                    "location": f"Page {chunk['page']}" if chunk.get("page") else chunk["source_id"],
                    "url": f"urn:distill:question-bank:{chunk['bank_id']}"})
        except Exception as error:
            logger.warning("Accuracy question-bank retrieval failed | error_type=%s", type(error).__name__)
            failures.append("question_banks")
    bounded, seen = [], set()
    for reference in references:
        text = " ".join(reference["text"].split()).casefold()
        if text not in seen and len(json.dumps([*bounded, reference], ensure_ascii=False)) <= 8000:
            bounded.append(reference)
            seen.add(text)
    status = "partial" if bounded and failures else "failed" if failures else "retrieved" if bounded else "no_relevant_matches"
    if not sources["documents"] and not sources["question_banks"]:
        status = "no_ready_sources"
    return {"status": status, "references": bounded, "failed_sources": failures}


async def prepare_references(state):
    """Persist per-group retrieval through existing assessment stages before evaluation."""
    from app.services.interviews import analysis

    sources = state.get("inputs", {}).get("reference_sources")
    if sources is None:
        # Existing runs and synthetic fixtures retain their original supplied references.
        return {"answer_references": state.get("references", []), "reference_retrieval": None}
    group = state["groups"][state["group_index"]]
    stage = f"references-{group['id']}"
    result = await analysis.stage_read(state, stage)
    if result is None:
        if group["answer_state"] in {"explicitly_unanswered", "interrupted", "unclear"}:
            result = {"status": "not_needed", "references": [], "failed_sources": []}
        else:
            result = await retrieve(state["recruiter_id"], question_query(state, group), sources)
        await analysis.stage_write(state, stage, result)
    registry = {reference["id"]: reference for reference in state.get("references", [])}
    registry.update({reference["id"]: reference for reference in result["references"]})
    return {"references": list(registry.values()), "answer_references": result["references"],
        "reference_retrieval": {key: value for key, value in result.items() if key != "references"}}
