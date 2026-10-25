"""Recruiter-facing history and exact, atomic reviewed editor saves."""
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select

from app.models.wind_tunnel import WindTunnelRun, WindTunnelSuggestion, WindTunnelTask, WindTunnelDocument, WindTunnelAssessment
from app.services.jd.edit_policy import document_edit_version, posting_status, LOCKED_POST_STATUSES, require_editable
from app.services.jd.editing import save_manual_edit
from app.services.jd.suggestions import SuggestionProposal, preview_suggestion
from app.services.recruitment_access import owned_job
from app.services.wind_tunnel import storage
from app.services.wind_tunnel.progress import progress_for
from app.services.wind_tunnel.generation import GENERATOR_VERSION, VALIDATOR_VERSION, RENDERER_VERSION
from app.services.wind_tunnel.suggestions import config_for

REVIEW_REASON_CODES = {"adds_evidence_burden", "adds_requirement", "unsupported_source",
                      "prose_profile_conflict", "unsupported_outcome_claim", "ungrouped_changes", "no_meaningful_change"}


async def history(db, job_id, owner_id):
    job = await owned_job(db, job_id, owner_id)
    version = document_edit_version(job)
    locked = await posting_status(db, job_id) in LOCKED_POST_STATUSES
    tasks = (await db.scalars(select(WindTunnelTask).where(WindTunnelTask.job_id == job_id,
        WindTunnelTask.owner_id == owner_id).order_by(WindTunnelTask.created_at.desc(), WindTunnelTask.id.desc()))).all()
    runs = {r.id: r for r in (await db.scalars(select(WindTunnelRun).where(
        WindTunnelRun.job_id == job_id, WindTunnelRun.owner_id == owner_id))).all()}
    proposals = (await db.scalars(select(WindTunnelSuggestion).where(WindTunnelSuggestion.run_id.in_(runs))
        .order_by(WindTunnelSuggestion.created_at, WindTunnelSuggestion.id))).all() if runs else []
    documents = (await db.scalars(select(WindTunnelDocument).where(WindTunnelDocument.run_id.in_(runs)))).all() if runs else []
    assessments = (await db.scalars(select(WindTunnelAssessment).where(WindTunnelAssessment.run_id.in_(runs)))).all() if runs else []
    batches = []
    for task in tasks:
        run = runs.get(task.run_id)
        analysis = run.resources.get("analysis", {}) if run else {}
        generation = run.resources.get("generation", {}) if run else {}
        suggestion_state = run.resources.get("suggestions", {}) if run else {}
        rejection_reasons = sorted({reason for detail in suggestion_state.get("validation_details", [])
                                    for reason in detail.get("rejection_reasons", []) if reason in REVIEW_REASON_CODES})
        config = run.versions.get("generation", {}) if run else {}
        old_configuration = any(config.get(k) != v for k, v in {"generator": GENERATOR_VERSION, "validator": VALIDATOR_VERSION, "renderer": RENDERER_VERSION}.items())
        if run and not old_configuration:
            old_configuration = run.versions.get("suggestions") != config_for({"generation": {"execution": config.get("execution")}})
        evaluation = run.resources.get("evaluations", {}).get(analysis.get("evaluation_id"), {}) if run else {}
        batches.append({"id": task.id, "run_id": task.run_id, "status": task.status,
            "stale": task.source_version != version, "trigger": task.trigger, "attempts": task.attempts,
            "error_code": task.error_code, "created_at": task.created_at, "updated_at": task.updated_at,
            "stage": (run.resources.get("suggestions", {}).get("status") or analysis.get("stage")) if run else None,
            "coverage": evaluation.get("coverage"),
            "needs_new_batch": bool(run and ((task.status in {"limited", "failed"} and old_configuration)
                or (task.status == "completed" and run.resources.get("suggestions", {}).get("status") == "no_suggestions"
                    and run.resources.get("suggestions", {}).get("guidance")))),
            "generation_failures": generation.get("failures", {}),
            "review_rejection_reasons": rejection_reasons,
            "requested_suggestions": suggestion_state.get("requested_suggestions"),
            "progress": progress_for(task, run, [d for d in documents if d.run_id == task.run_id],
                                     [a for a in assessments if a.run_id == task.run_id]),
            "suggestions": [{"id": p.id, **p.payload["proposal"],
                "affected_areas": p.payload.get("affected_areas", []), "basis": p.payload.get("basis", "diagnostic"),
                "effect_scope": p.payload.get("effect_scope"), "finding_ids": p.payload.get("finding_ids", []),
                "status": "stale" if p.status == "ready" and task.source_version != version else p.status,
                "applied_at": p.applied_at} for p in proposals if p.run_id == task.run_id]})
    return {"edit_version": version, "editing_locked": locked, "batches": batches}


async def preview(db, job_id, owner_id, ids, version, *, lock=False, document=None):
    job = document or await owned_job(db, job_id, owner_id)
    if job.id != job_id:
        raise HTTPException(404, "Job not found.")
    await require_editable(db, job)
    if version != document_edit_version(job):
        raise HTTPException(409, "This job changed. Reload and review current suggestions.")
    if len(ids) != len(set(ids)):
        raise HTTPException(422, "Select each suggestion once.")
    query = select(WindTunnelSuggestion, WindTunnelRun).join(WindTunnelRun).where(
        WindTunnelSuggestion.id.in_(ids), WindTunnelRun.job_id == job_id, WindTunnelRun.owner_id == owner_id)
    if lock:
        query = query.with_for_update(of=WindTunnelSuggestion).execution_options(populate_existing=True)
    pairs = (await db.execute(query)).all()
    if len(pairs) != len(ids):
        raise HTTPException(404, "Suggestion not found.")
    if len({run.id for _, run in pairs}) != 1:
        raise HTTPException(422, "Review suggestions from one validated batch at a time.")
    pairs.sort(key=lambda pair: str(pair[0].id))
    patches = []
    for row, run in pairs:
        if row.status != "ready" or run.source_version != version or run.status not in {"completed", "completed_with_issues"}:
            raise HTTPException(409, "This suggestion is no longer available to apply.")
        if not row.payload.get("validation", {}).get("requires_recruiter_review"):
            raise HTTPException(409, "This suggestion has not completed validation.")
        patches.extend(SuggestionProposal.model_validate(row.payload["proposal"]).patches)
    proposal = SuggestionProposal(title="Review suggested changes", reason="Review description and criteria together.", patches=patches)
    edit = preview_suggestion(job, proposal, version)
    payload = edit.model_dump(mode="json")
    token = storage.digest({"job": str(job_id), "ids": sorted(map(str, ids)), "edit": payload})
    return {"edit": payload, "review_token": token}, [p[0] for p in pairs]


async def apply(db, graph, job_id, owner_id, ids, version, review_token, reviewed):
    if not reviewed:
        raise HTTPException(422, "Review the description and screening criteria together before applying.")
    job = await owned_job(db, job_id, owner_id)
    prepared, _ = await preview(db, job_id, owner_id, ids, version)
    if prepared["review_token"] != review_token:
        raise HTTPException(409, "Review this exact suggestion again before applying.")
    from app.services.jd.editing import ManualEdit
    edit = ManualEdit.model_validate({**prepared["edit"], "criteria_reviewed": True})

    async def mark_applied(session, locked_document, incoming):
        # Called after the editor's AssistantConversation -> JDConversation ->
        # JobDescription locks, before mutations and its single SQL commit.
        final, rows = await preview(session, job_id, owner_id, ids, version, lock=True, document=locked_document)
        if final["review_token"] != review_token:
            raise HTTPException(409, "The reviewed suggestion changed. Review it again.")
        others = (await session.scalars(select(WindTunnelSuggestion).join(WindTunnelRun).where(
            WindTunnelRun.job_id == job_id, WindTunnelRun.owner_id == owner_id,
            WindTunnelSuggestion.status == "ready").with_for_update(of=WindTunnelSuggestion))).all()
        for other in others:
            other.status = "stale"
        for row in rows:
            row.status, row.applied_at = "applied", datetime.now(timezone.utc)

    return await save_manual_edit(db, graph, owner_id, job.conversation_id, edit, before_save=mark_applied)
