"""Owned F11 invocation, version pinning and SQL reconciliation on retry."""
from copy import deepcopy
from uuid import uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.wind_tunnel import WindTunnelSuggestion
from app.services.cv.screening import processing_versions
from app.services.wind_tunnel import generation, storage, suggestions
from app.services.wind_tunnel.evaluation import EVALUATOR_VERSION
from app.services.wind_tunnel.indexing import INDEX_ADAPTER_VERSION
from app.services.wind_tunnel.policy import REPETITIONS, personas_for, repetitions_for
from app.workflows.wind_tunnel.graph import build_wind_tunnel_graph

ORCHESTRATOR_VERSION = "synthetic-analysis-3"


async def analysis_versions():
    versions = await generation.generation_versions()
    return {**versions, "assessment_repetitions": REPETITIONS, "processing": await processing_versions(), "suggestions": suggestions.config_for(versions),
            "orchestrator": ORCHESTRATOR_VERSION, "evaluator": EVALUATOR_VERSION, "synthetic_index": INDEX_ADAPTER_VERSION}


async def run_analysis(run_id, owner_id, *, root=None, session_factory=None, saver=False, include_suggestions=False):
    factory = session_factory or SessionFactory
    root = root or get_settings().wind_tunnel_storage_path
    token, thread = str(uuid4()), f"wind-tunnel:{run_id}:analysis"
    async with factory() as db:
        run = await storage.owned_run(db, run_id, owner_id, lock=True, writable=True)
        if (run.versions.get("orchestrator") != ORCHESTRATOR_VERSION or run.versions.get("evaluator") != EVALUATOR_VERSION
                or run.versions.get("synthetic_index") != INDEX_ADAPTER_VERSION or "processing" not in run.versions):
            raise ValueError("SYNTHETIC_ANALYSIS_CONFIG_MISMATCH")
        previous = run.resources.get("analysis", {})
        proposal_state = run.resources.get("suggestions", {})
        reuse_analysis = previous.get("status") in {"completed", "completed_with_issues"} and include_suggestions
        if reuse_analysis:
            report = run.resources.get("evaluations", {}).get(previous.get("evaluation_id"), {})
            reuse_analysis = report.get("coverage", {}).get("completed_assessments") == len(personas_for(run.versions)) * repetitions_for(run.versions)
        if include_suggestions and run.versions.get("suggestions") != suggestions.config_for(run.versions):
            raise ValueError("SUGGESTION_CONFIG_MISMATCH")
        if include_suggestions and proposal_state.get("status") in {"completed", "no_suggestions"}:
            return {"status": run.status, "error_code": run.error_code, "analysis": deepcopy(run.resources["evaluations"][previous["evaluation_id"]]),
                    "suggestions": deepcopy(proposal_state)}
        if previous.get("status") == "completed" and not include_suggestions:
            return deepcopy(run.resources["evaluations"][previous["evaluation_id"]])
        if await db.scalar(select(WindTunnelSuggestion.id).where(WindTunnelSuggestion.run_id == run_id).limit(1)):
            raise HTTPException(409, "This diagnostic already has suggestions. Create a new run.")
        run.status, run.error_code = "running", None
        run.resources = {**run.resources, "analysis": {**previous, "token": token,
            "status": previous["status"] if reuse_analysis else "running", "error_code": None}}
        if saver is not False and saver is not None:
            run.resources = {**run.resources, "threads": sorted(set(run.resources.get("threads", []) + [thread]))}
        await db.commit()
    graph = build_wind_tunnel_graph(run_id, owner_id, token, root=root, session_factory=factory, saver=saver,
                                    include_suggestions=include_suggestions)
    try:
        # SQL reconciliation intentionally restarts nodes; no model results are
        # copied across repetitions, and successful stages skip their own work.
        await graph.ainvoke({"run_id": str(run_id), "reuse_analysis": reuse_analysis}, {"configurable": {"thread_id": thread}})
    except (ValueError, OSError, httpx.HTTPError, TimeoutError):
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            run.status, run.error_code = "failed", "SYNTHETIC_ANALYSIS_FAILED"
            run.resources = {**run.resources, "analysis": {**run.resources["analysis"], "status": "failed", "error_code": run.error_code}}
            await db.commit()
    async with factory() as db:
        run = await storage.owned_analysis(db, run_id, owner_id, token)
        analysis = run.resources["analysis"]
        if include_suggestions:
            report = run.resources.get("evaluations", {}).get(analysis.get("evaluation_id"))
            return {"status": run.status, "error_code": run.error_code, "analysis": deepcopy(report),
                    "suggestions": deepcopy(run.resources.get("suggestions", {}))}
        if analysis.get("status") in {"completed", "completed_with_issues"}:
            return deepcopy(run.resources["evaluations"][analysis["evaluation_id"]])
        return deepcopy(analysis)


async def run_wind_tunnel(run_id, owner_id, *, root=None, session_factory=None, saver=False):
    """Full pipeline; dispatched by the durable approval/More worker."""
    return await run_analysis(run_id, owner_id, root=root, session_factory=session_factory, saver=saver,
                              include_suggestions=True)
