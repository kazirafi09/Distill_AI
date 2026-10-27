"""Opt-in PostgreSQL selection tests; screening/model calls are mocked."""

import asyncio
import os
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import delete, select

from app.db.session import SessionFactory, engine
from app.models.cv import Candidate, CandidateApplication, CVSource
from app.models.cv_screening import CVAssessment, ScreeningRun
from app.models.interviews import InterviewShortlist, InterviewShortlistMember
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.services.interviews.schemas import SaveShortlistRequest
from app.services.interviews.selections import load_shortlist, save_shortlist
from app.services.interviews.shortlisting import ShortlistRequest, preview_shortlist


class SaveRequestTests(unittest.TestCase):
    def test_empty_selection_is_valid(self):
        request = SaveShortlistRequest(
            filters={"mode": "top_k", "k": 2},
            preview_key="a" * 64,
            selected_application_ids=[],
        )
        self.assertEqual(request.selected_application_ids, [])

    def test_duplicates_bad_keys_and_invalid_ids_are_rejected(self):
        identifier = uuid4()
        cases = [
            ("a" * 64, [identifier, identifier]),
            ("invalid", []),
            ("g" * 64, []),
            ("a" * 64, ["invalid"]),
        ]
        for key, ids in cases:
            with self.subTest(key=key, ids=ids), self.assertRaises(ValidationError):
                SaveShortlistRequest(
                    filters={"mode": "top_k", "k": 2},
                    preview_key=key,
                    selected_application_ids=ids,
                )


@unittest.skipUnless(os.environ.get("RUN_INTERVIEW_DB_TESTS") == "1", "Set RUN_INTERVIEW_DB_TESTS=1")
class SelectionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.user_ids = [uuid4(), uuid4()]
        self.addAsyncCleanup(self.cleanup_records)
        self.results = {"ranked": [], "incomplete": [], "warnings": []}
        async with SessionFactory() as db:
            for identifier in self.user_ids:
                db.add(User(id=identifier, email=f"shortlist-{identifier}@example.invalid",
                            password_hash="synthetic-test-only", role="recruiter"))
            await db.flush()
            self.owner, self.other = self.user_ids
            self.job_ids = []
            self.application_ids = []
            for owner in self.user_ids:
                conversation = JDConversation(recruiter_id=owner)
                db.add(conversation)
                await db.flush()
                job = JobDescription(conversation_id=conversation.id, title="Synthetic shortlist",
                                     content="Synthetic", requirements="Synthetic")
                db.add(job)
                await db.flush()
                self.job_ids.append(job.id)
                source = CVSource(job_description_id=job.id, drive_folder_id=f"shortlist-{uuid4()}")
                db.add(source)
                await db.flush()
                run = ScreeningRun(job_id=job.id, recruiter_id=owner, input_key="a" * 64,
                                   inputs={}, status="completed")
                db.add(run)
                await db.flush()
                for rank, (name, score) in enumerate(zip(("Alice", "Bob", "Carol"), ("92", "86", "81")), 1):
                    candidate = Candidate(recruiter_id=owner, full_name=name,
                                          email=f"{name.lower()}-{owner}@example.invalid")
                    db.add(candidate)
                    await db.flush()
                    application = CandidateApplication(candidate_id=candidate.id, job_description_id=job.id,
                                                       source_id=source.id, source_row_number=rank + 1)
                    db.add(application)
                    await db.flush()
                    assessment = CVAssessment(run_id=run.id, application_id=application.id,
                                              file_version_ids=[], status="completed", score={"aggregate": score})
                    db.add(assessment)
                    await db.flush()
                    if owner == self.owner:
                        self.application_ids.append(application.id)
                        self.results["ranked"].append({
                            "id": str(assessment.id), "application_id": str(application.id),
                            "name": name, "score": assessment.score, "rank": rank,
                            "status": "completed", "outdated": False,
                        })
                    else:
                        self.foreign_application_id = application.id
                if owner == self.owner:
                    self.results.update(job_id=str(job.id), run={"id": str(run.id), "status": "completed"})
            await db.commit()
        self.job_id = self.job_ids[0]
        self.filters = ShortlistRequest(mode="top_k", k=2)
        self.screening = AsyncMock(side_effect=lambda *args, **kwargs: deepcopy(self.results))
        self.screening_patches = []
        for target in (
            "app.services.interviews.shortlisting.get_screening_results",
            "app.services.interviews.selections.get_screening_results",
        ):
            mocked = patch(target, self.screening)
            mocked.start()
            self.screening_patches.append(mocked)
            self.addCleanup(mocked.stop)
        for target in (
            "app.services.interviews.shortlisting.available_processing_versions",
            "app.services.interviews.selections.available_processing_versions",
        ):
            mocked = patch(target, AsyncMock(return_value={"synthetic": "v1"}))
            mocked.start()
            self.screening_patches.append(mocked)
            self.addCleanup(mocked.stop)

    async def cleanup_records(self):
        try:
            async with SessionFactory() as db:
                await db.execute(delete(User).where(User.id.in_(self.user_ids)))
                await db.commit()
        finally:
            await engine.dispose()

    async def request(self, ids):
        preview = await preview_shortlist(self.job_id, self.owner, self.filters)
        return SaveShortlistRequest(filters=self.filters, preview_key=preview["preview_key"],
                                    selected_application_ids=ids)

    async def member_ids(self):
        async with SessionFactory() as db:
            return dict((await db.execute(
                select(InterviewShortlistMember.application_id, InterviewShortlistMember.id)
                .join(InterviewShortlist)
                .where(InterviewShortlist.job_description_id == self.job_id)
            )).all())

    async def test_no_saved_selection(self):
        self.assertIsNone(await load_shortlist(self.job_id, self.owner))

    async def test_save_reload_and_manual_addition(self):
        alice, _, carol = self.application_ids
        saved = await save_shortlist(self.job_id, self.owner, await self.request([alice, carol]))
        self.assertEqual(saved, await load_shortlist(self.job_id, self.owner))
        self.assertEqual(saved["selected_count"], 2)
        self.assertFalse(saved["outdated"])
        self.assertEqual([member["name"] for member in saved["members"]], ["Alice", "Carol"])
        self.assertEqual([member["manually_added"] for member in saved["members"]], [False, True])
        self.assertEqual([member["score"]["aggregate"] for member in saved["members"]], ["92", "81"])
        self.assertEqual([member["rank"] for member in saved["members"]], [1, 3])

    async def test_repeated_saves_reuse_records(self):
        request = await self.request(self.application_ids[:2])
        first = await save_shortlist(self.job_id, self.owner, request)
        before = await self.member_ids()
        second = await save_shortlist(self.job_id, self.owner, request)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(before, await self.member_ids())

    async def test_update_and_empty_selection(self):
        await save_shortlist(self.job_id, self.owner, await self.request(self.application_ids[:2]))
        updated = await save_shortlist(self.job_id, self.owner, await self.request(self.application_ids[2:]))
        self.assertEqual([member["name"] for member in updated["members"]], ["Carol"])
        empty = await save_shortlist(self.job_id, self.owner, await self.request([]))
        self.assertEqual(empty["members"], [])
        self.assertEqual(empty["selected_count"], 0)
        self.assertEqual(empty["id"], updated["id"])

    async def test_other_recruiter_cannot_save_or_load(self):
        request = await self.request(self.application_ids[:1])
        for operation in (
            lambda: save_shortlist(self.job_id, self.other, request),
            lambda: load_shortlist(self.job_id, self.other),
        ):
            with self.assertRaises(HTTPException) as denied:
                await operation()
            self.assertEqual(denied.exception.status_code, 404)

    async def test_cross_job_and_ineligible_applications_are_rejected(self):
        for identifier in (self.foreign_application_id, uuid4()):
            with self.subTest(identifier=identifier), self.assertRaises(HTTPException) as denied:
                await save_shortlist(self.job_id, self.owner, await self.request([identifier]))
            self.assertEqual(denied.exception.status_code, 422)
        self.assertIsNone(await load_shortlist(self.job_id, self.owner))

    async def test_database_membership_checked_even_if_preview_contains_foreign_application(self):
        foreign = deepcopy(self.results["ranked"][0])
        foreign["application_id"] = str(self.foreign_application_id)
        self.results["ranked"].append(foreign)
        with self.assertRaises(HTTPException) as denied:
            await save_shortlist(self.job_id, self.owner, await self.request([self.foreign_application_id]))
        self.assertEqual(denied.exception.status_code, 422)

    async def test_changed_preview_rejected(self):
        request = await self.request(self.application_ids[:1])
        self.results["ranked"][0]["score"] = {"aggregate": "90"}
        with self.assertRaises(HTTPException) as denied:
            await save_shortlist(self.job_id, self.owner, request)
        self.assertEqual(denied.exception.status_code, 409)
        self.assertIsNone(await load_shortlist(self.job_id, self.owner))

    async def test_change_during_save_rolls_back_existing_selection(self):
        original = await save_shortlist(self.job_id, self.owner, await self.request(self.application_ids[:1]))
        request = await self.request(self.application_ids[2:])
        changed = deepcopy(self.results)
        changed["ranked"][0]["score"] = {"aggregate": "90"}
        self.screening.side_effect = [deepcopy(self.results), changed]
        with self.assertRaises(HTTPException) as denied:
            await save_shortlist(self.job_id, self.owner, request)
        self.assertEqual(denied.exception.status_code, 409)
        self.screening.side_effect = lambda *args, **kwargs: deepcopy(self.results)
        self.assertEqual(original, await load_shortlist(self.job_id, self.owner))

    async def test_unavailable_or_outdated_results_rejected(self):
        for status, warnings in (("outdated", []), ("completed", ["VERSIONS_UNAVAILABLE"])):
            self.results["run"]["status"] = status
            self.results["warnings"] = warnings
            with self.subTest(status=status), self.assertRaises(HTTPException) as denied:
                await save_shortlist(self.job_id, self.owner, await self.request([]))
            self.assertEqual(denied.exception.status_code, 409)

    async def test_load_detects_changed_score_and_run(self):
        await save_shortlist(self.job_id, self.owner, await self.request(self.application_ids[:1]))
        self.results["ranked"][0]["score"] = {"aggregate": "90"}
        loaded = await load_shortlist(self.job_id, self.owner)
        self.assertTrue(loaded["outdated"])
        self.assertEqual(loaded["members"][0]["score"]["aggregate"], "92")
        self.results["run"]["id"] = str(uuid4())
        self.assertTrue((await load_shortlist(self.job_id, self.owner))["outdated"])

    async def test_concurrent_saves_do_not_duplicate_records(self):
        request = await self.request(self.application_ids[:2])
        first, second = await asyncio.gather(
            save_shortlist(self.job_id, self.owner, request),
            save_shortlist(self.job_id, self.owner, request),
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(await self.member_ids()), 2)
        async with SessionFactory() as db:
            shortlists = list(await db.scalars(select(InterviewShortlist).where(
                InterviewShortlist.job_description_id == self.job_id)))
        self.assertEqual(len(shortlists), 1)


if __name__ == "__main__":
    unittest.main()
