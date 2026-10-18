"""Report access control, immutable sources and full PDF content."""
from io import BytesIO
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import UUID

from fastapi import FastAPI
import httpx
from pypdf import PdfReader

from app.auth import require_recruiter, get_current_user
from app.assessment_routes import router
from app.db.session import SessionFactory
from app.models.assessments import AssessmentResult
from app.scripts import assessment_worker as worker
from app.services.interviews import full_report, consolidation as service, consolidation_runs as handoff
from app.services.interviews.report_pdf import render
import test_assessment_storage as storage
import test_consolidation_storage as consolidation


@unittest.skipUnless(os.environ.get("RUN_ASSESSMENT_DB_TESTS") == "1", "Set RUN_ASSESSMENT_DB_TESTS=1")
class AssessmentReportTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = storage.AssessmentDatabaseTests.asyncSetUp
    cleanup = storage.AssessmentDatabaseTests.cleanup
    start = storage.AssessmentDatabaseTests.start
    generate = consolidation.ConsolidationDatabaseTests.generate

    async def test_authenticated_pdf_route_checks_owner_and_completion(self):
        run = await self.start()
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            base = f"/assessments/runs/{run['id']}/report"
            for path in (base, base + ".pdf"):
                self.assertIn((await client.get(path)).status_code, (401, 403))
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(role="interviewer")
            self.assertEqual((await client.get(base + ".pdf")).status_code, 403)
            app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=self.other)
            self.assertEqual((await client.get(base + ".pdf")).status_code, 404)
            app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=self.owner)
            self.assertEqual((await client.get(base + ".pdf")).status_code, 409)
            await worker.run_once(UUID(run["id"]))
            response = await client.get(base + ".pdf")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertIn("attachment", response.headers["content-disposition"])
            self.assertTrue(response.content.startswith(b"%PDF-"))
            pages = PdfReader(BytesIO(response.content)).pages
            text = "\n".join(page.extract_text() for page in pages)
            self.assertIn("No CV assessment was pinned", text)
            self.assertIn("F6 consolidation is not included", text)
            self.assertIn("Not assessed", text)
            self.assertIn("0/100", text)
            report = (await client.get(base)).json()
            transcript = report["sections"][7]["blocks"]
            # Every segment appears once in the chronological appendix, in source order.
            actual = [b["text"] for b in transcript if b["kind"] == "text"]
            expected = [t["text"] for t in sorted(self.model.prepared["turns"], key=lambda t: (t["session_start_ms"], t["session_end_ms"], t["id"]))]
            self.assertEqual(actual, expected)
            self.assertNotIn("token_hash", str(report))
            self.assertNotIn("room_name", str(report))

    async def test_f6_report_uses_frozen_cv_and_f5_and_exports_all_sections(self):
        parent = await self.start()
        await worker.run_once(UUID(parent["id"]))
        child = await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation())
        with patch.object(service, "generate_json", side_effect=self.generate):
            await worker.run_once(UUID(child["id"]))
        doc = await full_report.document(UUID(child["id"]), self.owner)
        self.assertEqual(len(doc["sections"]), 9)
        self.assertIn("100/100", str(doc["sections"][3]))
        # A later mutation of the parent row cannot substitute new report content for F6's accepted snapshot.
        async with SessionFactory() as db:
            saved = await db.get(AssessmentResult, UUID(parent["id"]))
            saved.payload = {**saved.payload, "dimension_summaries": {}}
            await db.commit()
        again = await full_report.document(UUID(child["id"]), self.owner)
        self.assertEqual(doc["sections"], again["sections"])
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(render(doc))).pages)
        for title in ("Saved CV screening", "Detailed interview questions and answers", "Complete chronological interview transcript", "Provenance and limitations"):
            self.assertIn(title, text)
        # Data resembling HTML is literal in both formats and cannot become PDF markup.
        doc["sections"][0]["blocks"].append({"kind": "text", "text": "<script>sample & literal</script> " + "long-token-" * 500})
        safe = PdfReader(BytesIO(render(doc)))
        self.assertIn("<script>sample & literal</script>", "\n".join(p.extract_text() for p in safe.pages))
