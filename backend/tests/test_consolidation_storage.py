"""Owned F6 handoff and persisted graph checks on isolated PostgreSQL records."""
import asyncio
from copy import deepcopy
import hashlib
import json
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import select, func
from app.db.session import SessionFactory
from app.models.assessments import AssessmentRun, AssessmentResult, AssessmentStage
from app.models.cv import CVDocument, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.models.interviews import InterviewShortlist, InterviewShortlistMember
from app.scripts import assessment_worker as worker
from app.services.interviews import analysis, consolidation as service, consolidation_runs as handoff, assessment_runs as runs
import test_assessment_storage as storage_fixtures


@unittest.skipUnless(os.environ.get("RUN_ASSESSMENT_DB_TESTS") == "1", "Set RUN_ASSESSMENT_DB_TESTS=1")
class ConsolidationDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = storage_fixtures.AssessmentDatabaseTests.asyncSetUp
    cleanup = storage_fixtures.AssessmentDatabaseTests.cleanup
    start = storage_fixtures.AssessmentDatabaseTests.start
    add_real_transcript = storage_fixtures.AssessmentDatabaseTests.add_real_transcript
    mark_synthetic_transcript = storage_fixtures.AssessmentDatabaseTests.mark_synthetic_transcript

    async def parent(self):
        parent = await self.start()
        await worker.run_once(UUID(parent["id"]))
        return parent

    async def generate(self, system, payload, schema, *args, **kwargs):
        context = json.loads(payload)
        def findings(section):
            source = next(s for s in context["sources"] if s["criterion_ids"] and service.supports(section, s))
            return [{"criterion_id": source["criterion_ids"][0], "text": f"Test finding for {section}.", "source_ids": [source["id"]]}]
        if schema["title"] == "Findings":
            section = next(key for key in service.BRANCHES if service.INSTRUCTIONS[key] in system)
            return json.dumps({"findings": findings(section)})
        suitability = {"position": "mixed_evidence", "rationale": "Synthetic test conclusion.", "source_ids": ["cv-1"]}
        if schema["title"] == "Suitability":
            return json.dumps(suitability)
        return json.dumps({**{key: findings(key) for key in service.BRANCHES}, "suitability": suitability})

    async def test_owned_duplicate_handoff_and_saved_separate_scores(self):
        parent = await self.parent()
        with self.assertRaises(HTTPException) as error:
            await handoff.start(UUID(parent["id"]), self.other, handoff.StartConsolidation())
        self.assertEqual(error.exception.status_code, 404)
        first, second = await asyncio.gather(*[handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation()) for _ in range(2)])
        self.assertEqual(first["id"], second["id"])
        with patch.object(service, "generate_json", side_effect=self.generate) as model:
            await worker.run_once(UUID(first["id"]))
        self.assertEqual(model.await_count, 5)
        result = await runs.run_result(UUID(first["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertEqual(result["run"]["kind"], "f6")
        self.assertEqual(result["run"]["completed_groups"], 4)
        self.assertEqual(len(result["answers"]), 7)
        self.assertEqual(result["result"]["component_scores"]["cv"]["aggregate"], "100")
        self.assertEqual(result["result"]["component_scores"]["interview"], result["dimension_summaries"])
        self.assertIsNone(result["run"]["application_id"])
        self.assertEqual(len(self.model.calls), 26)  # No F5 rerun or CV model call.

    async def test_pending_parent_and_changed_artifact_are_blocked(self):
        parent = await self.start()
        with self.assertRaises(HTTPException):
            await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation())
        await worker.run_once(UUID(parent["id"]))
        async with SessionFactory() as db:
            result = await db.get(AssessmentResult, UUID(parent["id"]))
            result.payload = {**result.payload, "artifact_sha256": "0" * 64}
            await db.commit()
        with self.assertRaises(HTTPException):
            await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation())

    async def test_parent_change_after_enqueue_prevents_model_calls(self):
        parent = await self.parent()
        child = await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation())
        async with SessionFactory() as db:
            result = await db.get(AssessmentResult, UUID(parent["id"]))
            result.payload = {**result.payload, "artifact_sha256": "0" * 64}
            await db.commit()
        with patch.object(service, "generate_json", new_callable=AsyncMock) as model:
            await worker.run_once(UUID(child["id"]))
        model.assert_not_awaited()
        result = await runs.run_status(UUID(child["id"]), self.owner)
        self.assertEqual(result["error_code"], "F6_SOURCE_CHANGED")
        self.assertFalse(result["can_retry"])

    async def test_invalid_report_repairs_are_persisted_and_not_restarted(self):
        parent = await self.parent()
        child = await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation())
        with patch.object(service, "generate_json", AsyncMock(return_value="{}")) as model:
            await worker.run_once(UUID(child["id"]))
        self.assertEqual(model.await_count, 6)  # Four invalid branches and exactly two whole-draft repairs.
        result = await runs.run_result(UUID(child["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "needs_review")
        self.assertNotIn("findings", result["result"])
        async with SessionFactory() as db:
            repairs = list(await db.scalars(select(AssessmentStage.name).where(AssessmentStage.run_id == UUID(child["id"]),
                AssessmentStage.name.in_(["f6-repair-1", "f6-repair-2"]))))
            self.assertEqual(len(repairs), 2)
        self.assertFalse(await worker.run_once(UUID(child["id"])))

    async def test_real_cv_and_review_confirmation_use_exact_pinned_results(self):
        await self.add_real_transcript()
        async with SessionFactory() as db:
            # Controlled database-only CV fixture; no real candidate data or file downloads.
            demo = handoff.synthetic_cv(type("Parent", (), {"inputs": {"prepared": self.model.prepared, "criteria": self.model.input["criteria"]}})())
            text = demo["documents"][0]["text"]
            sha = hashlib.sha256(text.encode()).hexdigest()
            document = CVDocument(job_description_id=self.job, application_id=self.application, drive_file_id=f"f6-test-{uuid4()}")
            db.add(document)
            await db.flush()
            file = CVFileVersion(cv_document_id=document.id, original_filename="synthetic-cv.txt", mime_type="text/plain",
                relative_path=f"tests/{uuid4()}.txt", file_size_bytes=len(text.encode()), file_sha256=sha, status="downloaded")
            db.add(file)
            await db.flush()
            parsed = CVParseResult(file_version_id=file.id, processing_key="f" * 64, file_sha256=sha, versions={},
                status="completed", text=text, sources=demo["documents"][0]["sources"])
            db.add(parsed)
            await db.flush()
            screening = ScreeningRun(job_id=self.job, recruiter_id=self.owner, input_key="f" * 64, status="completed",
                inputs={"profile": {"criteria": self.model.input["criteria"]}, "profile_version": 1})
            db.add(screening)
            await db.flush()
            matches = deepcopy(demo["matches"])
            for match in matches:
                for quote in match["evidence"]:
                    quote["parse_id"], quote["file_version_id"] = str(parsed.id), str(file.id)
            cv = CVAssessment(run_id=screening.id, application_id=self.application, file_version_ids=[str(file.id)],
                parse_result_ids=[str(parsed.id)], status="completed", matches=matches, score=demo["score"])
            db.add(cv)
            await db.flush()
            shortlist = InterviewShortlist(job_description_id=self.job, recruiter_id=self.owner, screening_run_id=screening.id,
                filter_inputs={}, proposed_application_ids=[str(self.application)])
            db.add(shortlist)
            await db.flush()
            db.add(InterviewShortlistMember(shortlist_id=shortlist.id, application_id=self.application, assessment_id=cv.id,
                score_snapshot=cv.score, rank_snapshot=1, manually_added=False))
            cv_id = cv.id
            await db.commit()
        parent = await runs.start_run(self.job, self.owner, runs.StartAssessment(application_id=self.application))
        await worker.run_once(UUID(parent["id"]))
        with self.assertRaises(HTTPException):
            await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation())
        child = await handoff.start(UUID(parent["id"]), self.owner, handoff.StartConsolidation(confirm_f5_review=True))
        with patch.object(service, "generate_json", side_effect=self.generate):
            await worker.run_once(UUID(child["id"]))
        result = await runs.run_result(UUID(child["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "completed")
        self.assertFalse(result["run"]["synthetic"])
        self.assertEqual(result["result"]["provenance"]["cv_assessment_id"], str(cv_id))
        self.assertEqual(result["result"]["component_scores"]["cv"], demo["score"])
        self.assertFalse(result["result"]["publishable"])

        # Named graph tests may use an owned saved CV, but synthetic interview results must remain isolated.
        await self.mark_synthetic_transcript()
        sample = await runs.start_run(self.job, self.owner, runs.StartAssessment(application_id=self.application))
        await worker.run_once(UUID(sample["id"]))
        sample_child = await handoff.start(UUID(sample["id"]), self.owner, handoff.StartConsolidation())
        with patch.object(service, "generate_json", side_effect=self.generate):
            await worker.run_once(UUID(sample_child["id"]))
        sample_result = await runs.run_result(UUID(sample_child["id"]), self.owner)
        self.assertEqual(sample_result["run"]["status"], "completed")
        self.assertTrue(sample_result["run"]["synthetic"])
        self.assertIsNone(sample_result["run"]["application_id"])
        self.assertEqual(sample_result["result"]["component_scores"]["cv"], demo["score"])
        self.assertNotIn("SYNTHETIC_CV_NOT_MODEL_SCREENED", sample_result["result"]["warnings"])
