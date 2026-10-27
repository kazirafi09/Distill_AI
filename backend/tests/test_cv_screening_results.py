"""Regression checks for screening results without live models or PostgreSQL."""
import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.models.cv import Candidate, CandidateApplication, CVFileVersion
from app.models.cv_parsing import CVParseResult
from app.services.cv import screening


class ScreeningResultsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.job_id = uuid4()
        self.recruiter_id = uuid4()
        self.inputs = {"profile": {"criteria": []}, "versions": {"model": "test"}}
        self.job = SimpleNamespace(screening_profile=self.inputs["profile"])
        self.run = SimpleNamespace(
            id=uuid4(), job_id=self.job_id, status="completed_with_issues",
            input_key=screening.digest(self.inputs), inputs=self.inputs,
            attempt=1, warnings=[], error_code=None,
            updated_at=datetime.now(timezone.utc),
        )
        self.assessments = [self.assessment("80"), self.assessment(None, "failed")]

    def assessment(self, score, status="completed"):
        return SimpleNamespace(
            id=uuid4(), application_id=uuid4(), status=status,
            file_version_ids=[], parse_result_ids=[], index_ids=[],
            score={"aggregate": score} if score is not None else None,
            matches=[], warnings=[], error_code=None, reused_from=None,
        )

    async def results(self, runs=None, inputs_error=None, parse_status="completed", integrity_error=None):
        runs = [self.run] if runs is None else runs
        db = AsyncMock()
        db.no_autoflush = MagicMock()
        db.expire_all = MagicMock()
        # Run list, assessments for each run, then refreshed latest assessments.
        db.scalars.side_effect = [runs] + [self.assessments for _ in runs] + (
            [self.assessments] if runs else []
        )

        async def get_record(model, record_id):
            if model is CandidateApplication:
                return SimpleNamespace(candidate_id=record_id)
            if model is Candidate:
                return SimpleNamespace(full_name="Test candidate")
            if model is CVParseResult:
                if parse_status is None:
                    return None
                return SimpleNamespace(status=parse_status, file_version_id=uuid4())
            if model is CVFileVersion:
                return SimpleNamespace(id=record_id)
            raise AssertionError(f"Unexpected model: {model}")

        db.get.side_effect = get_record
        context = AsyncMock()
        context.__aenter__.return_value = db
        with patch.object(screening, "SessionFactory", return_value=context), \
             patch.object(screening, "owned_job", AsyncMock(return_value=self.job)), \
             patch.object(screening, "available_processing_versions", AsyncMock(return_value={"model": "test"})), \
             patch.object(screening, "inputs_for", AsyncMock(return_value=self.inputs, side_effect=inputs_error)), \
             patch.object(screening, "verify_integrity", side_effect=integrity_error):
            result = await screening.get_screening_results(self.job_id, self.recruiter_id)
        db.commit.assert_awaited_once()
        return result

    async def test_current_results_keep_response_and_separate_failed_assessments(self):
        result = await self.results()
        self.assertEqual(set(result), {
            "job_id", "run", "profile", "versions", "rubric",
            "ranked", "incomplete", "history", "warnings",
        })
        self.assertEqual(result["job_id"], str(self.job_id))
        self.assertEqual(result["ranked"][0], {
            "id": str(self.assessments[0].id),
            "application_id": str(self.assessments[0].application_id),
            "name": "Test candidate", "status": "completed", "outdated": False,
            "file_version_ids": [], "parse_result_ids": [], "index_ids": [],
            "score": {"aggregate": "80"}, "matches": [], "warnings": [],
            "error_code": None, "reused_from": None, "rank": 1,
        })
        self.assertEqual(result["incomplete"][0]["status"], "failed")
        self.assertEqual(result["run"], screening.run_public(self.run, self.assessments))
        self.assertEqual(result["profile"], self.inputs["profile"])
        self.assertEqual(result["versions"], self.inputs["versions"])
        self.assertEqual(result["warnings"], [])

    async def test_missing_invalid_scores_are_withheld_and_zero_is_ranked(self):
        self.assessments = [self.assessment(value) for value in
                            (None, "bad", "NaN", "Infinity", "-1", "101", "0")]
        result = await self.results()
        self.assertEqual(len(result["ranked"]), 1)
        self.assertEqual(result["ranked"][0]["score"]["aggregate"], "0")
        self.assertEqual(len(result["incomplete"]), 6)

    def test_score_validation_handles_non_objects_and_booleans(self):
        for score in (None, [], {}, {"aggregate": True}):
            self.assertFalse(screening.valid_aggregate_score(score))

    async def test_changed_inputs_mark_runs_and_results_outdated(self):
        self.run.input_key = "old inputs"
        result = await self.results()
        self.assertEqual(self.run.status, "outdated")
        self.assertEqual(result["ranked"], [])
        self.assertTrue(all(item["outdated"] for item in result["incomplete"]))

    async def test_unavailable_versions_withhold_ranking_without_marking_current_run_outdated(self):
        result = await self.results(inputs_error=RuntimeError("Models unavailable"))
        self.assertEqual(result["ranked"], [])
        self.assertEqual(len(result["incomplete"]), 2)
        self.assertEqual(self.run.status, "completed_with_issues")
        self.assertEqual(result["warnings"], ["CURRENT_MODEL_VERSIONS_UNAVAILABLE_RANKING_WITHHELD"])

    async def test_provider_cooldown_is_bounded_and_cancellation_propagates(self):
        async def blocked():
            await asyncio.Event().wait()

        with patch.object(screening, "processing_versions", blocked), \
             patch.object(screening, "VERSION_DISCOVERY_TIMEOUT_SECONDS", 0.01):
            self.assertIsNone(await screening.available_processing_versions())
        with patch.object(screening, "processing_versions", AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):
                await screening.available_processing_versions()

    async def test_existing_transaction_never_discovers_provider_versions(self):
        db = AsyncMock()
        db.no_autoflush = MagicMock()
        db.expire_all = MagicMock()
        with patch.object(screening, "cached_processing_versions", return_value=None), \
             patch.object(screening, "processing_versions", AsyncMock()) as discover, \
             patch.object(screening, "_screening_results_in_session", AsyncMock(return_value={})) as read:
            await screening.get_screening_results(self.job_id, self.recruiter_id, db=db)
        discover.assert_not_awaited()
        read.assert_awaited_once_with(db, self.job_id, self.recruiter_id, None)

    async def test_missing_or_incomplete_parse_marks_run_outdated(self):
        self.assessments[0].parse_result_ids = [str(uuid4())]
        for status in (None, "failed"):
            with self.subTest(parse_status=status):
                self.run.status = "completed_with_issues"
                result = await self.results(parse_status=status)
                self.assertEqual(result["ranked"], [])
                self.assertEqual(result["run"]["status"], "outdated")

    async def test_failed_file_integrity_marks_run_outdated(self):
        self.assessments[0].parse_result_ids = [str(uuid4())]
        result = await self.results(integrity_error=ValueError("File changed"))
        self.assertEqual(result["ranked"], [])
        self.assertEqual(result["run"]["status"], "outdated")

    async def test_empty_runs_use_job_profile(self):
        result = await self.results(runs=[])
        self.assertIsNone(result["run"])
        self.assertIsNone(result["versions"])
        self.assertEqual(result["profile"], self.job.screening_profile)
        for key in ("ranked", "incomplete", "history"):
            self.assertEqual(result[key], [])

    async def test_previous_runs_are_summaries_and_changed_inputs_are_persisted(self):
        old_run = SimpleNamespace(**{**vars(self.run), "id": uuid4(), "input_key": "old inputs"})
        result = await self.results(runs=[self.run, old_run])
        self.assertEqual(result["history"], [screening.run_public(old_run, self.assessments)])
        self.assertEqual(old_run.status, "outdated")
        self.assertEqual(len(result["ranked"]), 1)

    async def test_ownership_is_checked_before_loading_results(self):
        db = AsyncMock()
        db.no_autoflush = MagicMock()
        db.expire_all = MagicMock()
        context = AsyncMock()
        context.__aenter__.return_value = db
        with patch.object(screening, "SessionFactory", return_value=context), \
             patch.object(screening, "owned_job", AsyncMock(side_effect=HTTPException(404, "Job not found."))) as owned:
            with self.assertRaises(HTTPException) as raised:
                await screening.get_screening_results(self.job_id, self.recruiter_id)
        self.assertEqual(raised.exception.status_code, 404)
        owned.assert_awaited_once_with(db, self.job_id, self.recruiter_id)
        db.scalars.assert_not_awaited()
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
