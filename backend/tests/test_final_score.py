"""Combined scores must preserve missing evidence and exact source scope."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.services.interviews.analysis import digest
from app.services.interviews.final_score import combine, for_run


def performance():
    return {"summary": {"overall_score_100": "55", "assessed_weight": 100, "assessed_dimensions": 6,
                        "policy": {"essential_dimensions": ["technical_competency"]}},
            "dimensions": {"technical_competency": {"score": 1}},
            "provenance": {"f5_artifact_sha256": "f5"}}


def seal(payload):
    return {**payload, "artifact_sha256": digest(payload)}


class FinalScoreTests(unittest.TestCase):
    def test_agreed_formula_preserves_precision(self):
        result = combine({"aggregate": "78.666666666666666666666666667"}, performance())
        self.assertEqual(result["score_100"], "62.10")
        self.assertEqual(result["cv_contribution"], "23.60")
        self.assertEqual(result["interview_contribution"], "38.50")
        self.assertEqual(result["status"], "provisional")

    def test_missing_inputs_are_not_replaced_by_zero_or_a_qa_average(self):
        for cv, interview in [(None, performance()), ({"aggregate": "78.67"}, None), ({"aggregate": "NaN"}, performance())]:
            result = combine(cv, interview)
            self.assertIsNone(result["score_100"])
            self.assertEqual(result["status"], "incomplete")
        result = combine({"aggregate": "0"}, performance())
        self.assertEqual(result["score_100"], "38.50")

    def test_coverage_and_technical_gates(self):
        for weight, count, technical in [(79, 6, 1), (100, 3, 1), (100, 6, None)]:
            interview = performance()
            interview["summary"].update(assessed_weight=weight, assessed_dimensions=count)
            interview["dimensions"]["technical_competency"]["score"] = technical
            self.assertIsNone(combine({"aggregate": "78.67"}, interview)["score_100"])

    def test_review_flags_are_visible_without_arbitrary_score_deductions(self):
        result = combine({"aggregate": "78.67", "must_have_review_flags": ["experience"]}, performance(), ["Transcript gaps."])
        self.assertEqual(result["score_100"], "62.10")
        self.assertEqual(len(result["warnings"]), 2)


class SourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_projection_requires_matching_immutable_interview_source(self):
        run = SimpleNamespace(id="report", kind="f6", parent_run_id="parent", status="completed", recruiter_id="owner",
            job_id="job", application_id="app", synthetic=False, inputs={"cv": {"score": {"aggregate": "78.67"}}, "f5": {"artifact_sha256": "f5"}})
        latest = SimpleNamespace(id="interview", status="completed", created_at=datetime.now(timezone.utc))
        db = SimpleNamespace(scalar=AsyncMock(return_value=latest), get=AsyncMock(return_value=SimpleNamespace(payload=seal(performance()))))
        result = await for_run(db, run, seal({"status": "completed"}))
        self.assertEqual(result["score_100"], "62.10")
        self.assertEqual(result["interview_run_id"], "interview")
        wrong = deepcopy(performance())
        wrong["provenance"]["f5_artifact_sha256"] = "another-f5"
        db.get.return_value = SimpleNamespace(payload=seal(wrong))
        self.assertIsNone((await for_run(db, run, seal({"status": "completed"})))["score_100"])

    async def test_newer_pending_analysis_does_not_silently_reuse_an_older_score(self):
        run = SimpleNamespace(id="parent", kind="f5", status="completed", recruiter_id="owner", job_id="job",
            application_id="app", synthetic=False, inputs={"cv_snapshot": {"score": {"aggregate": "78.67"}}})
        db = SimpleNamespace(scalar=AsyncMock(return_value=SimpleNamespace(id="pending", status="queued", created_at=datetime.now(timezone.utc))), get=AsyncMock())
        result = await for_run(db, run, seal({"status": "completed"}))
        self.assertEqual(result["status"], "incomplete")
        db.get.assert_not_awaited()
