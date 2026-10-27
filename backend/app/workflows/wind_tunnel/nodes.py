"""F11 node callbacks; injected runtime dependencies stay outside graph state."""
from copy import deepcopy
from dataclasses import asdict
import json
from time import perf_counter

import httpx
from sqlalchemy import select

from app.models.wind_tunnel import WindTunnelAssessment, WindTunnelDocument
from app.services.cv.matching import validate_match
from app.services.cv.profile_schema import validate_profile
from app.services.cv.screening import processing_versions
from app.services.wind_tunnel import generation, storage, suggestions
from app.services.wind_tunnel.evaluation import evaluate
from app.services.wind_tunnel.extraction import apply_extraction
from app.services.wind_tunnel.indexing import SyntheticIndex
from app.services.wind_tunnel.policy import repetitions_for
from app.workflows.wind_tunnel.assessment import load_assessment_graph
from app.workflows.wind_tunnel.extraction import ensure_extraction


def provider_failure(exc):
    return isinstance(exc, (httpx.HTTPError, TimeoutError, OSError)) or str(exc) in {
        "MODEL_UNAVAILABLE", "MODEL_TIMEOUT", "MODEL_AUTH_FAILED", "MODEL_RATE_LIMITED", "MODEL_NOT_AVAILABLE",
        "MODEL_API_KEY_MISSING", "CV_MODEL_NOT_INSTALLED", "SCREENING_MODEL_NOT_INSTALLED", "SYNTHETIC_INDEX_UNAVAILABLE"}


def build_nodes(run_id, owner_id, token, *, root, session_factory, saver=False, include_suggestions=False):
    factory = session_factory
    index = SyntheticIndex(run_id, owner_id, token, root=root, session_factory=factory)

    async def stage(name, **updates):
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            run.resources = {**run.resources, "analysis": {**run.resources["analysis"], "stage": name, **updates}}
            await db.commit()
        return {"stage": name}

    async def load(state):
        if state["run_id"] != str(run_id):
            raise ValueError("SYNTHETIC_ANALYSIS_SCOPE_INVALID")
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            expected = deepcopy(run.versions["processing"])
            await db.commit()
        if await processing_versions() != expected:
            raise ValueError("SYNTHETIC_PROCESSING_VERSIONS_CHANGED")
        return await stage("load_snapshot")

    async def generate(state):
        await stage("generate_suite")
        result = await generation.generate_suite(run_id, owner_id, root=root, session_factory=factory)
        if result.get("error_code") == "CV_DOCUMENT_INTEGRITY_FAILED":
            raise ValueError("CV_DOCUMENT_INTEGRITY_FAILED")
        progress = await stage("generate_suite", generation_status=result["status"])
        return {**progress, "stop_dispatch": result.get("error_code") == "CV_GENERATION_PROVIDER_FAILED"}

    async def extract(state):
        await stage("prepare_extraction")
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            accepted = run.resources["generation"]["documents"]
            documents = list(await db.scalars(select(WindTunnelDocument).where(WindTunnelDocument.run_id == run_id)))
            for document in documents:
                if document.persona_id not in accepted or not accepted[document.persona_id].get("validated"):
                    raise ValueError("SYNTHETIC_SUITE_NOT_VALIDATED")
                for repetition in range(1, repetitions_for(run.versions) + 1):
                    await storage.create_assessment(db, run_id, document.id, owner_id, repetition)
            await db.commit()
        errors = {}
        for document in documents:
            try:
                await ensure_extraction(run_id, document.id, owner_id, token, root=root, session_factory=factory)
            except (ValueError, OSError, TimeoutError):
                errors[str(document.id)] = "SYNTHETIC_EXTRACTION_FAILED"
        return await stage("prepare_extraction", extraction_errors=errors)

    async def assess(state):
        if state.get("stop_dispatch"):
            return await stage("assess_cohorts", provider_stopped=True)
        await stage("assess_cohorts", provider_stopped=False)
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            extractions = deepcopy(run.resources.get("extractions", {}))
            rows = list(await db.scalars(select(WindTunnelAssessment).where(
                WindTunnelAssessment.run_id == run_id, WindTunnelAssessment.phase == "baseline")
                .order_by(WindTunnelAssessment.repetition, WindTunnelAssessment.document_id)))
            await db.commit()
        for row in rows:
            # Only this exact repetition's completed model outputs can be reused.
            if row.payload.get("status") == "completed":
                continue
            entry = extractions.get(str(row.document_id))
            if entry is None or entry.get("status") != "completed":
                await fail_assessment(row.id, "SYNTHETIC_EXTRACTION_FAILED")
                continue
            graph, _, config = await load_assessment_graph(run_id, row.id, owner_id,
                index.retrieve, index.prepare, root=root, saver=saver, session_factory=factory,
                extraction=entry, writer_token=token)
            started = perf_counter()
            try:
                await graph.ainvoke({"assessment_id": str(row.id)}, config)
            except (ValueError, httpx.HTTPError, TimeoutError, OSError) as exc:
                await fail_assessment(row.id, "SYNTHETIC_ASSESSMENT_FAILED")
                if provider_failure(exc):
                    return await stage("assess_cohorts", provider_stopped=True)
            async with factory() as db:
                run = await storage.owned_analysis(db, run_id, owner_id, token)
                analysis = run.resources["analysis"]
                run.resources = {**run.resources, "analysis": {**analysis, "assessment_seconds": {
                    **analysis.get("assessment_seconds", {}), str(row.id): perf_counter() - started}}}
                await db.commit()
        return {"stage": "assess_cohorts"}

    async def fail_assessment(identifier, code):
        # Retain only this repetition's last committed parsing/matching work.
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            row = await db.get(WindTunnelAssessment, identifier, populate_existing=True)
            document = await db.get(WindTunnelDocument, row.document_id)
            record = storage.restore_assessment(row, storage.source_for(run, document), run.versions["processing"]["parsing"])
            record.status, record.error_code = "failed", code
            row.payload, row.revision = json.loads(storage.canonical(asdict(record))), row.revision + 1
            await db.commit()

    async def evaluate_results(state):
        await stage("evaluate_findings")
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            rows = list(await db.scalars(select(WindTunnelAssessment).where(
                WindTunnelAssessment.run_id == run_id, WindTunnelAssessment.phase == "baseline")
                .order_by(WindTunnelAssessment.repetition, WindTunnelAssessment.document_id)))
            documents = {d.id: d for d in await db.scalars(select(WindTunnelDocument).where(WindTunnelDocument.run_id == run_id))}
            snapshot, plan = deepcopy(run.snapshot), deepcopy(run.resources["generation"].get("expectations"))
            repetitions = repetitions_for(run.versions)
            results = []
            for row in rows:
                document = documents[row.document_id]
                payload = row.payload
                if payload.get("status") == "completed":
                    _, source, _ = await storage.owned_document(db, run_id, document.id, owner_id, root=root)
                    record = storage.restore_assessment(row, source, run.versions["processing"]["parsing"])
                    apply_extraction(record.parsed, run.resources["extractions"][str(document.id)], source,
                                     run.versions["processing"]["parsing"])
                    validate_profile(record.parsed.profile, record.parsed.text, record.parsed.sources)
                    criteria = {c["id"]: c for c in snapshot["screening_profile"]["criteria"]}
                    for match in record.matches:
                        criterion = criteria.get(match["requirement_id"])
                        if criterion is None or validate_match(match, criterion, [record.parsed]) != match:
                            raise ValueError("SYNTHETIC_MATCH_INTEGRITY_FAILED")
                results.append({"assessment_id": str(row.id), "document_id": str(document.id), "persona": document.persona_id,
                    "repetition": row.repetition, "status": payload.get("status", "pending"),
                    "matches": payload.get("matches", []), "score": payload.get("score"), "error_code": payload.get("error_code")})
            await db.commit()
        if plan is None:
            # Missing/invalid criteria never manufacture expectations or findings.
            return await stage("limited", status="limited", error_code="SYNTHETIC_EXPECTATIONS_UNAVAILABLE")
        report, findings = evaluate(snapshot, plan, results, repetitions=repetitions)
        report["assessments"] = [{k: v for k, v in item.items() if k not in {"matches", "score"}} for item in results]
        report["finding_ids"] = [f.id for f in findings]
        identifier = storage.digest(report)
        async with factory() as db:
            run = await storage.owned_analysis(db, run_id, owner_id, token)
            await storage.save_findings(db, run_id, owner_id, findings, append=True)
            run.resources = {**run.resources, "evaluations": {**run.resources.get("evaluations", {}), identifier: report},
                "analysis": {**run.resources["analysis"], "stage": "analysis_complete", "status": report["status"],
                             "evaluation_id": identifier, "error_code": None}}
            await db.commit()
        return {"stage": "analysis_complete"}

    async def draft_proposals(state):
        await stage("generate_suggestions")
        result = await suggestions.draft_suggestions(run_id, owner_id, token, root=root, session_factory=factory)
        return {"stage": "generate_suggestions", "suggestion_status": result["status"]}

    async def validate_proposals(state):
        await stage("validate_suggestions")
        result = await suggestions.review_suggestions(run_id, owner_id, token, root=root, session_factory=factory)
        return {"stage": "validate_suggestions", "suggestion_status": result["status"]}

    async def persist_proposals(state):
        await stage("suggestions_complete")
        result = await suggestions.persist_suggestions(run_id, owner_id, token, root=root, session_factory=factory)
        return {"stage": "suggestions_complete", "suggestion_status": result["status"]}

    nodes = [("load_snapshot", load), ("generate_suite", generate), ("prepare_extraction", extract),
             ("assess_cohorts", assess), ("evaluate_findings", evaluate_results)]
    if include_suggestions:
        nodes += [("generate_suggestions", draft_proposals), ("validate_suggestions", validate_proposals),
                  ("persist_suggestions", persist_proposals)]
    return nodes
