"""Bounded recovery reuses validated work without resetting the original repair budget."""
import json
import os
from uuid import UUID
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import select
from app.db.session import SessionFactory
from app.models.assessments import AssessmentRun, AssessmentStage, AssessmentAnswer
from app.scripts import assessment_worker as worker
from app.services.interviews import assessment_runs as runs, analysis
import test_assessment_storage as fixtures


@unittest.skipUnless(os.environ.get("RUN_ASSESSMENT_DB_TESTS") == "1", "Set RUN_ASSESSMENT_DB_TESTS=1")
class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AssessmentDatabaseTests.asyncSetUp
    cleanup = fixtures.AssessmentDatabaseTests.cleanup
    start = fixtures.AssessmentDatabaseTests.start

    async def invalid(self, system, payload, schema, *args, **kwargs):
        raw = await self.model.generate(system, payload, schema, *args, **kwargs)
        if schema["title"] == "DimensionEvaluation" and "Evaluate ONLY completeness" in system and json.loads(payload)["group"]["id"] == "g6":
            output = json.loads(raw)
            output.update(score=None, reason="ASSESSED")
            return json.dumps(output)
        return raw

    async def failed_source(self):
        source = await self.start()
        with patch.object(analysis, "generate_json", side_effect=self.invalid):
            await worker.run_once(UUID(source["id"]))
        result = await runs.run_result(UUID(source["id"]), self.owner)
        self.assertEqual(result["run"]["status"], "needs_review")
        self.assertEqual(len(result["answers"]), 5)
        return source, result

    async def test_one_new_evaluation_and_original_history_preserved(self):
        source, original = await self.failed_source()
        with self.assertRaises(HTTPException) as denied:
            await runs.recover_run(UUID(source["id"]), self.other)
        self.assertEqual(denied.exception.status_code, 404)
        first = await runs.recover_run(UUID(source["id"]), self.owner)
        second = await runs.recover_run(UUID(source["id"]), self.owner)
        self.assertEqual(first["id"], second["id"])
        with patch.object(analysis, "generate_json", side_effect=self.model.generate) as model:
            await worker.run_once(UUID(first["id"]))
        self.assertEqual(model.await_count, 1)
        request = model.call_args
        self.assertIn("numeric score", request.args[0])
        self.assertIn("not proof of a mapped job criterion", request.args[0])
        final = await runs.run_result(UUID(first["id"]), self.owner)
        self.assertEqual(final["run"]["status"], "completed")
        self.assertEqual(final["answers"][:5], original["answers"])
        self.assertEqual(final["result"]["recovery"]["source_run_id"], source["id"])
        self.assertFalse(final["run"]["can_recover"])
        self.assertEqual((await runs.run_status(UUID(source["id"]), self.owner))["status"], "needs_review")
        async with SessionFactory() as db:
            attempts = await db.get(AssessmentStage, (UUID(source["id"]), "evaluation-g6-completeness-attempts"))
            self.assertEqual(attempts.payload["count"], 2)
            row = await db.get(AssessmentRun, UUID(first["id"]))
            self.assertEqual(row.inputs["recovery"]["retry_stage"], "evaluation-g6-completeness")
        # A repeated click can only return the same completed child.
        self.assertEqual((await runs.recover_run(UUID(source["id"]), self.owner))["id"], first["id"])

    async def test_recovery_exhaustion_cannot_create_another_generation(self):
        source, _ = await self.failed_source()
        child = await runs.recover_run(UUID(source["id"]), self.owner)
        with patch.object(analysis, "generate_json", side_effect=self.invalid) as model:
            await worker.run_once(UUID(child["id"]))
        self.assertEqual(model.await_count, 2)
        self.assertEqual((await runs.run_status(UUID(child["id"]), self.owner))["status"], "needs_review")
        with self.assertRaises(HTTPException):
            await runs.recover_run(UUID(child["id"]), self.owner)
        self.assertEqual((await runs.recover_run(UUID(source["id"]), self.owner))["id"], child["id"])
