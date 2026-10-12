"""Focused real-services check with retained synthetic fixtures for browser QA.

Use create, check, then cleanup. Never prints access tokens or real CV content.
"""
import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID
from decimal import Decimal
from unittest.mock import AsyncMock, patch
import httpx
from sqlalchemy import delete, select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.security import create_access_token, hash_password
from app.db.session import SessionFactory
from app.models.cv import Candidate, CandidateApplication, CVDocument, CVSource
from app.models.jd import JobDescription
from app.models.user import User
from app.models.cv_screening import CVAssessment, CVIndex, ScreeningRun
from app.services.cv.indexing import collection, search_evidence
from app.services.cv.scoring import calculate_scores
from app.services.cv.screening import get_screening_results, resume_run, retrieve_through_mcp, start_job_screening
from app.workflows.cv_screening import process_candidate
from test_cv_parsing_postgres import PersistenceTests
from test_cv_scoring import profile

MARKER = Path("/tmp/distill-cv-screening-check.json")
PASSWORD = "Synthetic-CV-Screening-2026!"


async def main():
    mode = sys.argv[1]
    fixture = PersistenceTests("test_model_timeout_retains_text")
    if mode == "create":
        if MARKER.exists():
            raise RuntimeError("Clean up prior fixture first")
        await fixture.asyncSetUp()
        data = {"ids": [str(v) for v in fixture.ids], "users": [str(v) for v in fixture.users],
                "paths": [str(p) for p in fixture.paths], "job_id": str(fixture.job_id)}
        MARKER.write_text(json.dumps(data))
        async with SessionFactory() as db:
            owner = await db.get(User, fixture.owner)
            owner.email, owner.password_hash = f"cv-screen-check-{fixture.owner}@example.com", hash_password(PASSWORD)
            job = await db.get(JobDescription, fixture.job_id)
            job.screening_profile, job.profile_version = profile(), 1
            # Keep real local matching verification to one approved criterion.
            job.screening_profile = {**job.screening_profile, "criteria": job.screening_profile["criteria"][:1]}
            source = await db.scalar(select(CVSource).where(CVSource.job_description_id == job.id))
            source.spreadsheet_id, source.sheet_tab_id = f"synthetic-{job.id}", 0
            original = await db.scalar(select(CandidateApplication).where(CandidateApplication.job_description_id == job.id))
            good = await db.get(Candidate, original.candidate_id)
            good.full_name = "Synthetic Alice"
            bad = Candidate(recruiter_id=fixture.owner, full_name="Synthetic unreadable", email=f"unreadable-{fixture.owner}@example.com")
            db.add(bad); await db.flush()
            application = CandidateApplication(candidate_id=bad.id, job_description_id=job.id, source_id=source.id, source_row_number=3)
            db.add(application); await db.flush()
            from app.models.cv import CVFileVersion
            corrupt = await db.get(CVFileVersion, fixture.ids[1])
            doc = await db.get(CVDocument, corrupt.cv_document_id)
            doc.application_id = application.id
            data.update(application_id=str(original.id), other_application_id=str(application.id))
            await db.commit()
            MARKER.write_text(json.dumps(data))
            print(json.dumps({"email": owner.email, "password": PASSWORD, "job_id": str(job.id), "jd_number": job.number}))
        first, duplicate = await asyncio.gather(start_job_screening(fixture.job_id, fixture.owner), start_job_screening(fixture.job_id, fixture.owner))
        assert first["id"] == duplicate["id"], "Duplicate starts created different runs"
        data["run_id"] = first["id"]
        MARKER.write_text(json.dumps(data))
        print(json.dumps(first))
        return
    data = json.loads(MARKER.read_text())
    owner, other = UUID(data["users"][0]), UUID(data["users"][1])
    job_id, app_id, run_id = UUID(data["job_id"]), UUID(data["application_id"]), UUID(data["run_id"])
    if mode == "status":
        results = await get_screening_results(job_id, owner)
        print(json.dumps({"run": results["run"], "applications": [{"status": a["status"], "error": a["error_code"], "warnings": a["warnings"]} for a in results["ranked"] + results["incomplete"]]}))
    elif mode == "resume":
        print(json.dumps(await resume_run(run_id, owner)))
    elif mode == "start":
        run = await start_job_screening(job_id, owner)
        data["run_id"] = run["id"]
        MARKER.write_text(json.dumps(data))
        print(json.dumps(run))
    elif mode == "check":
        results = await get_screening_results(job_id, owner)
        print(json.dumps({"run": results["run"], "ranked": len(results["ranked"]), "incomplete": len(results["incomplete"])}))
        assert results["run"]["status"] == "completed_with_issues", "Wait for worker completion, then rerun check"
        assert len(results["ranked"]) == 1 and len(results["incomplete"]) == 1
        item = results["ranked"][0]
        assert item["matches"][0]["decision"] in {"met", "partially_met", "insufficient_evidence"}, item["matches"]
        assert item["matches"][0]["evidence"] and "Python" in item["matches"][0]["evidence"][0]["quote"]
        assert calculate_scores(results["profile"], item["matches"])["aggregate"] == item["score"]["aggregate"]
        print(json.dumps({"actual_model_decision": item["matches"][0]["decision"], "actual_score": item["score"]["aggregate"]}))
        evidence = await retrieve_through_mcp(job_id, app_id, "Python", owner, UUID(item["id"]))
        assert evidence["evidence"] and all(e["file_version_id"] in item["file_version_ids"] for e in evidence["evidence"])
        wrong_scope = await search_evidence(job_id, UUID(data["other_application_id"]), "Python", owner, UUID(item["id"]))
        assert not wrong_scope["evidence"]
        try:
            await retrieve_through_mcp(job_id, UUID(data["other_application_id"]), "Python", owner, UUID(item["id"]))
        except ValueError:
            pass
        else:
            raise AssertionError("MCP workflow credential must not retrieve another application's evidence")
        token = create_access_token(other)
        async with httpx.AsyncClient(base_url="http://backend:8000") as client:
            denied = await client.get(f"/cv/screening/jobs/{job_id}/results", headers={"Authorization": f"Bearer {token}"})
            assert denied.status_code == 404
            unauth = await client.get(f"/cv/screening/runs/{run_id}")
            assert unauth.status_code == 401
        from mcp import ClientSession
        from mcp.client.streamable_http import streamable_http_client
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
            async with streamable_http_client("http://backend:8000/mcp/", http_client=client) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    denied = await session.call_tool("search_cv_evidence", {"job_id": str(job_id), "application_id": str(app_id), "query": "Python"})
                    assert denied.isError
        duplicate = await start_job_screening(job_id, owner)
        assert duplicate["id"] == str(run_id)
        async with SessionFactory() as db:
            saved = await db.get(CVAssessment, UUID(item["id"]))
            previous = saved.updated_at
        await process_candidate(run_id, UUID(item["id"]))
        async with SessionFactory() as db:
            saved = await db.get(CVAssessment, UUID(item["id"]))
            assert saved.updated_at == previous
            # Interrupt matching after committed parsing/indexing. The next call
            # resumes the actual PG checkpoint and retains the index/profile.
            saved.status, saved.matches, saved.score, saved.reused_from = "pending", [], None, None
            saved.attempt += 1
            await db.commit()
        started = asyncio.Event()
        async def wait_before_model(*args):
            started.set()
            await asyncio.Event().wait()
        with patch("app.workflows.cv_screening.match_criterion", AsyncMock(side_effect=wait_before_model)):
            task = asyncio.create_task(process_candidate(run_id, UUID(item["id"])))
            await asyncio.wait_for(started.wait(), timeout=30)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        async with SessionFactory() as db:
            saved = await db.get(CVAssessment, UUID(item["id"]))
            assert saved.status == "matching" and saved.index_ids and saved.parse_result_ids
        with patch("app.workflows.cv_screening.parse_cv", AsyncMock(side_effect=AssertionError("Parsing must not repeat"))), \
             patch("app.workflows.cv_screening.match_criterion", AsyncMock(return_value=(item["matches"][0], []))):
            await process_candidate(run_id, UUID(item["id"]))
        async with SessionFactory() as db:
            saved = await db.get(CVAssessment, UUID(item["id"]))
            assert saved.status == "completed" and saved.score["aggregate"] == item["score"]["aggregate"]
        print("PASS: real indexing/MCP/Qwen/scoring; arithmetic; isolation; ownership; duplicate reuse; interrupted checkpoint resume (injected interruption/resumed model response).")
    elif mode == "cleanup":
        async with SessionFactory() as db:
            runs = list(await db.scalars(select(ScreeningRun.id).where(ScreeningRun.job_id == job_id)))
            assessments = list(await db.scalars(select(CVAssessment).where(CVAssessment.run_id.in_(runs))))
            for assessment in assessments:
                for index_id in assessment.index_ids:
                    index = await db.get(CVIndex, UUID(index_id))
                    if index:
                        collection(index.versions).delete(where={"index_id": index_id})
            for prefix in [*(f"cv-screen:{v}:" for v in runs), *(f"cv-assess:{a.id}:" for a in assessments)]:
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    await db.execute(text(f"DELETE FROM {table} WHERE thread_id LIKE :prefix"), {"prefix": prefix + "%"})
            await db.commit()
        fixture.ids = [UUID(v) for v in data["ids"]]
        fixture.users = [UUID(v) for v in data["users"]]
        fixture.paths = [Path(p) for p in data["paths"]]
        await fixture.asyncTearDown()
        MARKER.unlink()
        print("Synthetic screening fixtures and scoped Chroma chunks removed")
    else:
        raise ValueError("Use create, check, cleanup")


if __name__ == "__main__":
    asyncio.run(main())
