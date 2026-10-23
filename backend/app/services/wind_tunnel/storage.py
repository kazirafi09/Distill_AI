"""Owned F11 persistence; request operations flush, callers commit.

No queue/model execution here. Graph callbacks open short independent sessions;
no transaction spans model calls. Existing interview/candidate tables are unused.
"""
import json
import os
import shutil
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Awaitable, Callable
from uuid import UUID, uuid5

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.wind_tunnel import WindTunnelRun, WindTunnelDocument, WindTunnelAssessment, WindTunnelSuggestion
from app.services.cv.processing import ParsedSource
from app.services.cv.profile_schema import validate_profile
from app.services.cv.scoring import RUBRIC, validate_scoring_profile
from app.services.documents.extraction import MAX_FILE_BYTES
from app.services.jd.edit_policy import document_edit_version
from app.services.jd.suggestions import SuggestionProposal, preview_suggestion
from app.services.recruitment_access import lock_job, owned_job
from app.services.wind_tunnel.sources import SyntheticAssessment, SyntheticDocument, SyntheticScope

ANALYSIS_VERSION = "wind-tunnel-1"
worker_claim = ContextVar("wind_tunnel_worker_claim", default=None)
worker_stop = ContextVar("wind_tunnel_worker_stop", default=None)


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      default=lambda item: str(item) if isinstance(item, UUID) else _unsupported(item))


def _unsupported(item):
    raise TypeError(f"Unsupported payload type: {type(item).__name__}")


def digest(value):
    return sha256(canonical(value).encode()).hexdigest()


async def owned_run(db, run_id, owner_id, *, lock=False, writable=False):
    query = select(WindTunnelRun).where(WindTunnelRun.id == run_id, WindTunnelRun.owner_id == owner_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    run = await db.scalar(query)
    if run is None:
        raise HTTPException(404, "Diagnostic run not found.")
    await owned_job(db, run.job_id, owner_id)
    if writable and run.status in {"deleting", "cleanup_failed"}:
        raise HTTPException(409, "Diagnostic cleanup is pending.")
    dispatch = run.resources.get("dispatch")
    if writable and dispatch:
        from app.models.wind_tunnel import WindTunnelTask
        from app.services.wind_tunnel.queue import now
        task = await db.scalar(select(WindTunnelTask).where(WindTunnelTask.id == UUID(dispatch["task_id"])))
        stop = worker_stop.get()
        if (worker_claim.get() != dispatch["claim"] or (stop is not None and stop.is_set())
                or not task or task.status != "running" or task.claim != dispatch["claim"]
                or not task.lease_until or task.lease_until <= now()):
            raise HTTPException(409, "This diagnostic worker no longer owns the batch.")
    return run


async def owned_analysis(db, run_id, owner_id, token):
    run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
    if run.resources.get("analysis", {}).get("token") != token:
        raise HTTPException(409, "This diagnostic analysis has another writer. Reload it.")
    return run


async def create_run(db, job_id, owner_id, versions, *, trigger="approval", request_id=None):
    if trigger not in {"approval", "more"} or (trigger == "more" and not request_id):
        raise ValueError("INVALID_DIAGNOSTIC_TRIGGER")
    await lock_job(db, job_id)
    job = await owned_job(db, job_id, owner_id)
    validate_scoring_profile(job.screening_profile, job.profile_version)
    version = document_edit_version(job)
    key = digest([trigger, request_id] if trigger == "more" else [trigger, version, ANALYSIS_VERSION, versions])
    existing = await db.scalar(select(WindTunnelRun).where(WindTunnelRun.job_id == job_id,
                                                          WindTunnelRun.request_key == key))
    if existing:
        if existing.source_version != version or existing.versions != versions or existing.analysis_version != ANALYSIS_VERSION:
            raise HTTPException(409, "This diagnostic request belongs to an earlier job version.")
        return await owned_run(db, existing.id, owner_id, writable=True)
    run = WindTunnelRun(owner_id=owner_id, job_id=job_id, request_key=key, source_version=version,
                        analysis_version=ANALYSIS_VERSION, trigger=trigger, versions=json.loads(canonical(versions)),
                        snapshot=json.loads(canonical({"content": job.content, "screening_profile": job.screening_profile,
                            "profile_version": job.profile_version, "requirements": job.requirements, "rubric": RUBRIC})))
    db.add(run)
    await db.flush()
    return run


def source_for(run, row):
    return SyntheticDocument(SyntheticScope(run.owner_id, run.job_id, run.id), row.id, row.file_sha256, row.suffix)


def write_document(root, document, data):
    path = document.path(root, document.scope)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        document.verify(root, document.scope)
        return
    temporary = None
    try:
        with NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


async def store_document(db, run_id, owner_id, persona_id, data, suffix, *, root=None):
    run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
    if suffix not in {".pdf", ".docx"} or not 0 < len(data) <= MAX_FILE_BYTES or not 0 < len(persona_id) <= 60:
        raise ValueError("INVALID_SYNTHETIC_DOCUMENT")
    identifier, file_hash = uuid5(run.id, persona_id), sha256(data).hexdigest()
    row = await db.get(WindTunnelDocument, identifier)
    if row and (row.file_sha256 != file_hash or row.suffix != suffix):
        raise HTTPException(409, "This persona already has a different immutable document.")
    if row is None:
        row = WindTunnelDocument(id=identifier, run_id=run.id, persona_id=persona_id,
                                 file_sha256=file_hash, suffix=suffix, size_bytes=len(data))
        db.add(row)
    await run_in_threadpool(write_document, root or get_settings().wind_tunnel_storage_path, source_for(run, row), data)
    await db.flush()
    return row


async def owned_document(db, run_id, document_id, owner_id, *, root=None):
    run = await owned_run(db, run_id, owner_id)
    row = await db.scalar(select(WindTunnelDocument).where(WindTunnelDocument.id == document_id,
                                                          WindTunnelDocument.run_id == run.id))
    if row is None:
        raise HTTPException(404, "Synthetic document not found.")
    document = source_for(run, row)
    path = await run_in_threadpool(document.verify, root or get_settings().wind_tunnel_storage_path, document.scope)
    if path.stat().st_size != row.size_bytes:
        raise ValueError("SYNTHETIC_DOCUMENT_INTEGRITY_FAILED")
    return run, document, path


async def create_assessment(db, run_id, document_id, owner_id, repetition):
    run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
    document = await db.scalar(select(WindTunnelDocument).where(WindTunnelDocument.run_id == run.id,
                                                               WindTunnelDocument.id == document_id))
    if document is None or repetition not in (1, 2, 3):
        raise ValueError("INVALID_SYNTHETIC_REPLICATE")
    existing = await db.scalar(select(WindTunnelAssessment).where(WindTunnelAssessment.document_id == document_id,
                                 WindTunnelAssessment.phase == "baseline", WindTunnelAssessment.repetition == repetition))
    if existing:
        return existing
    row = WindTunnelAssessment(run_id=run.id, document_id=document_id, repetition=repetition, payload={})
    db.add(row)
    await db.flush()
    return row


def restore_assessment(row, document, versions):
    if not row.payload:
        return SyntheticAssessment(ParsedSource(row.id, document.id, document.file_sha256, versions), id=row.id)
    values = dict(row.payload)
    parsed = dict(values.pop("parsed"))
    parsed.update(id=UUID(parsed["id"]), file_version_id=UUID(parsed["file_version_id"]))
    if parsed["file_version_id"] != document.id or parsed["file_sha256"] != document.file_sha256 or parsed["versions"] != versions:
        raise ValueError("SYNTHETIC_RESULT_SOURCE_MISMATCH")
    if UUID(values["id"]) != row.id or parsed["id"] != row.id:
        raise ValueError("SYNTHETIC_RESULT_ID_MISMATCH")
    return SyntheticAssessment(parsed=ParsedSource(**parsed), **{**values, "id": UUID(values["id"])})


@dataclass(frozen=True)
class AssessmentContext:
    document: SyntheticDocument
    record: SyntheticAssessment
    profile: dict
    profile_version: int
    rubric: dict
    persist: Callable[[SyntheticAssessment], Awaitable[None]]
    config: dict


async def load_assessment_context(run_id, assessment_id, owner_id,
                                  *, root=None, register_checkpoint=False, session_factory=None, extraction=None, writer_token=None):
    """Load owned evidence and a short SQL-save callback; workflows build graphs."""
    factory = session_factory or SessionFactory
    root = root or get_settings().wind_tunnel_storage_path
    async with factory() as db:
        run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
        row = await db.scalar(select(WindTunnelAssessment).where(WindTunnelAssessment.id == assessment_id,
                                                                WindTunnelAssessment.run_id == run.id))
        if row is None:
            raise HTTPException(404, "Synthetic assessment not found.")
        _, document, _ = await owned_document(db, run_id, row.document_id, owner_id, root=root)
        versions = run.versions.get("processing", {}).get("parsing", run.versions)
        record, revision = restore_assessment(row, document, versions), row.revision
        if writer_token is not None and run.resources.get("analysis", {}).get("token") != writer_token:
            raise HTTPException(409, "This diagnostic analysis has another writer. Reload it.")
        if extraction is not None:
            from app.services.wind_tunnel.extraction import apply_extraction
            apply_extraction(record.parsed, extraction, document, run.versions["processing"]["parsing"])
        profile, profile_version, rubric = (run.snapshot[k] for k in ("screening_profile", "profile_version", "rubric"))
        thread = f"wind-tunnel:{run.id}:{row.id}"
        if register_checkpoint:
            run.resources = {**run.resources, "threads": sorted(set(run.resources.get("threads", []) + [thread]))}
        await db.commit()

    async def persist(current):
        nonlocal revision
        async with factory() as db:
            current_run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
            if writer_token is not None and current_run.resources.get("analysis", {}).get("token") != writer_token:
                raise HTTPException(409, "This diagnostic analysis has another writer. Reload it.")
            saved = await db.get(WindTunnelAssessment, assessment_id, populate_existing=True)
            if saved is None or saved.revision != revision:
                raise HTTPException(409, "This diagnostic assessment has another writer. Reload it.")
            if current.parsed.status == "completed":
                validate_profile(current.parsed.profile, current.parsed.text, current.parsed.sources)
            saved.payload, saved.revision = json.loads(canonical(asdict(current))), revision + 1
            await db.commit()
            revision += 1

    return AssessmentContext(document, record, profile, profile_version, rubric, persist,
                             {"configurable": {"thread_id": thread}})


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assessment_id: UUID
    requirement_id: str = Field(min_length=1, max_length=100)


class StoredFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=500)
    references: list[EvidenceReference] = Field(min_length=1, max_length=100)


async def save_findings(db, run_id, owner_id, findings: list[StoredFinding], *, append=False):
    run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
    if len({f.id for f in findings}) != len(findings):
        raise ValueError("DUPLICATE_FINDING_ID")
    for finding in findings:
        for reference in finding.references:
            row = await db.scalar(select(WindTunnelAssessment).where(WindTunnelAssessment.id == reference.assessment_id,
                                                                     WindTunnelAssessment.run_id == run.id))
            if row is None or row.payload.get("status") != "completed" or not any(
                    m["requirement_id"] == reference.requirement_id for m in row.payload.get("matches", [])):
                raise ValueError("FINDING_EVIDENCE_NOT_IN_RUN")
    values = [f.model_dump(mode="json") for f in findings]
    if append:
        existing = {item["id"]: item for item in run.findings}
        if any(item["id"] in existing and item != existing[item["id"]] for item in values):
            raise HTTPException(409, "Stored findings are immutable. Create a new diagnostic run.")
        values = run.findings + [item for item in values if item["id"] not in existing]
    if not append and run.findings and run.findings != values:
        raise HTTPException(409, "Stored findings are immutable. Create a new diagnostic run.")
    run.findings = values
    await db.flush()


@dataclass(frozen=True)
class JobSnapshot:
    content: str
    screening_profile: dict
    profile_version: int


async def store_suggestion(db, run_id, owner_id, proposal: SuggestionProposal, finding_ids: list[str]):
    run = await owned_run(db, run_id, owner_id, lock=True, writable=True)
    if not finding_ids or not set(finding_ids) <= {f["id"] for f in run.findings}:
        raise ValueError("SUGGESTION_FINDINGS_NOT_IN_RUN")
    preview_suggestion(JobSnapshot(**{k: run.snapshot[k] for k in ("content", "screening_profile", "profile_version")}),
                       proposal, run.source_version)
    key = digest(sorted(canonical(p) for p in proposal.model_dump(mode="json")["patches"]))
    row = await db.scalar(select(WindTunnelSuggestion).where(WindTunnelSuggestion.run_id == run.id,
                                                           WindTunnelSuggestion.proposal_key == key))
    if row is None:
        row = WindTunnelSuggestion(run_id=run.id, proposal_key=key,
                                   payload={"proposal": proposal.model_dump(mode="json"), "finding_ids": sorted(set(finding_ids))})
        db.add(row)
        await db.flush()
    return row


async def cleanup_run(run_id, owner_id, *, root=None, saver=None, remove_vectors=None, session_factory=None):
    factory = session_factory or SessionFactory
    async with factory() as db:
        run = await owned_run(db, run_id, owner_id, lock=True)
        run.status, run.error_code = "deleting", None
        scope, resources = SyntheticScope(run.owner_id, run.job_id, run.id), dict(run.resources)
        await db.commit()
    try:
        if resources.get("vectors"):
            if remove_vectors is None:
                raise ValueError("SYNTHETIC_VECTOR_CLEANUP_REQUIRED")
            await remove_vectors(scope, resources["vectors"])
        if resources.get("threads"):
            if saver is None:
                raise ValueError("SYNTHETIC_CHECKPOINT_CLEANUP_REQUIRED")
            for thread in resources["threads"]:
                if not thread.startswith(f"wind-tunnel:{run_id}:"):
                    raise ValueError("SYNTHETIC_CHECKPOINT_SCOPE_INVALID")
                await saver.adelete_thread(thread)
        base = (root or get_settings().wind_tunnel_storage_path).resolve()
        directory = base / str(scope.owner_id) / str(scope.job_id) / str(scope.run_id)
        if directory.resolve() != directory or not directory.is_relative_to(base):
            raise ValueError("SYNTHETIC_CLEANUP_PATH_INVALID")
        if directory.exists():
            await run_in_threadpool(shutil.rmtree, directory)
        async with factory() as db:
            await owned_run(db, run_id, owner_id, lock=True)
            await db.execute(delete(WindTunnelRun).where(WindTunnelRun.id == run_id))
            await db.commit()
    except Exception:
        async with factory() as db:
            run = await owned_run(db, run_id, owner_id, lock=True)
            run.status, run.error_code = "cleanup_failed", "SYNTHETIC_CLEANUP_FAILED"
            await db.commit()
        raise
