"""Read-only presentation projections and truthful available actions; no provider calls."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from app.services.interviews import assessment_runs as runs
from app.services.interviews import post_interview_runs as extensions
from app.services.interviews import post_interview_analysis as analysis
from app.services.interviews import post_interview_report as report


def run(kind="interview_performance", status="completed", **changes):
    now = datetime.now(timezone.utc)
    values = dict(id=uuid4(), job_id=uuid4(), recruiter_id=uuid4(), parent_run_id=None,
        application_id=None, synthetic=True, label="Test", kind=kind, status=status,
        stage=status, attempt=1, completed_groups=1, total_groups=1, error_code=None,
        inputs={"prompt_version": analysis.PROMPT_VERSION, "rubric_version": analysis.VERSIONS.get(kind, "test")},
        heartbeat_at=None, created_at=now, updated_at=now, next_attempt_at=now)
    return SimpleNamespace(**{**values, **changes})


class AvailableActionTests(TestCase):
    def test_exhausted_validation_and_saved_results_never_offer_unchanged_rerun(self):
        for status in ("completed", "queued", "running", "needs_review"):
            row = run(status=status)
            self.assertEqual(extensions.available_action(row)["action"], "none")
        self.assertEqual(extensions.available_action(None)["action"], "start")
        self.assertEqual(extensions.available_action(None, "No pinned CV")["action"], "none")

    def test_retry_respects_version_source_and_attempt_budget(self):
        row = run(status="failed", error_code="MODEL_RATE_LIMITED")
        self.assertEqual(extensions.available_action(row)["action"], "retry")
        for change in ({"attempt": 6}, {"error_code": "POST_INTERVIEW_SOURCE_CHANGED"},
                       {"inputs": {"prompt_version": "old-version"}}):
            self.assertEqual(extensions.available_action(run(status="failed", **change))["action"], "none")

    def test_key_construction_retains_existing_deduplication_contract(self):
        parent = run()
        base = {"parent_run_id": str(parent.id), "f5": {"artifact_sha256": "saved"}}
        options, policy, cv = {"model": "test"}, {"revision": 0}, {"id": "pinned"}
        for kind in analysis.KINDS:
            expected = {**base, "prompt_version": analysis.PROMPT_VERSION,
                "rubric_version": analysis.VERSIONS[kind], "model_options": options,
                "f5_reviewer_id": str(parent.recruiter_id)}
            if kind == "cv_claim_review":
                expected["cv"] = cv
            if kind == "interview_performance":
                expected["scoring_settings"] = policy
            key = runs.digest({"kind": kind, "job_id": str(parent.job_id), **expected})
            actual = extensions.feature_inputs(base, parent, kind, options, policy, cv)
            self.assertEqual(actual, {**expected, "source_key": key})


class SourceProjectionTests(IsolatedAsyncioTestCase):
    async def test_f5_and_f6_return_only_their_pinned_cv_and_do_not_mutate_inputs(self):
        for kind, field in (("f5", "cv_snapshot"), ("f6", "cv")):
            cv = {"id": "pinned-old", "score": {"aggregate": "83.33"},
                "matches": [{"requirement_id": "pytest", "decision": "insufficient_evidence"}],
                "documents": [{"text": "Do not copy full document into this projection"}]}
            inputs = {field: cv, "prepared": {"turns": [], "source_version": 4},
                "criteria": [], "profile_version": 2, "profile_snapshot": {}}
            row = run(kind=kind, inputs=inputs)
            before = deepcopy(inputs)
            db = MagicMock()
            db.get = AsyncMock(return_value=None)
            db.scalars = AsyncMock(return_value=[])
            db.scalar = AsyncMock(return_value=None)
            factory = MagicMock()
            factory.return_value.__aenter__ = AsyncMock(return_value=db)
            factory.return_value.__aexit__ = AsyncMock(return_value=False)
            with patch.object(runs, "SessionFactory", factory), \
                 patch.object(runs, "owned_run", AsyncMock(return_value=row)), \
                 patch.object(runs, "owned_job", AsyncMock(return_value=SimpleNamespace(profile_version=2, screening_profile={}))), \
                 patch("app.services.interviews.consolidation_runs.eligibility", AsyncMock(return_value={})), \
                 patch("app.services.interviews.final_score.for_run", AsyncMock(return_value={})):
                result = await runs.run_result(row.id, row.recruiter_id)
            self.assertEqual(result["pinned_cv"]["id"], "pinned-old")
            self.assertEqual(result["pinned_cv"]["matches"][0]["decision"], "insufficient_evidence")
            self.assertNotIn("documents", result["pinned_cv"])
            self.assertEqual(result["source_metadata"]["transcript_version"], 4)
            result["pinned_cv"]["matches"].clear()
            self.assertEqual(row.inputs, before)

    async def test_legacy_synthetic_measured_result_is_labeled_as_authored_in_report(self):
        row = run(kind="interview_quality", parent_run_id=uuid4())
        payload = {"warnings": [], "process_summary": "Sample", "coverage": [], "findings": [],
            "speaking_time": {"status": "measured", "candidate_ms": 60000, "recruiter_ms": 10000, "note": "old label"}}
        payload["artifact_sha256"] = runs.digest(payload)
        db = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(payload=payload)))
        document = {"metadata": {}, "sections": []}
        with patch.object(report, "owned_run", AsyncMock(return_value=row)):
            await report.append(db, document, row.parent_run_id, row.recruiter_id, [row.id], False)
        text = str(document["sections"])
        self.assertIn("Authored synthetic intervals", text)
        self.assertNotIn("Measured speech segments", text)
        self.assertEqual(payload["speaking_time"]["status"], "measured")


class TimingTests(TestCase):
    def test_synthetic_real_and_unavailable_timing_are_distinct(self):
        prepared = {"turns": [{"speaker_role": "candidate", "session_start_ms": 0,
            "session_end_ms": 1000, "timing_basis": "synthetic_authoring_ground_truth"}]}
        self.assertEqual(analysis.speaking_time(prepared)["status"], "synthetic")
        prepared["turns"][0]["timing_basis"] = "finalized_session_timeline"
        self.assertEqual(analysis.speaking_time(prepared)["status"], "measured")
        prepared["text_only"] = True
        self.assertEqual(analysis.speaking_time(prepared)["status"], "unavailable")
