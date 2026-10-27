"""F7 contracts, image limits, field mapping and opt-in PostgreSQL lifecycle."""
import asyncio
import base64
from datetime import timedelta
from io import BytesIO
import os
import secrets
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException
from PIL import Image
from sqlalchemy import delete

from app.auth import require_recruiter
from app.cv_routes import router as cv_router
from app.db.session import SessionFactory, engine
from app.identification_routes import router
from app.models.candidate_identity import CandidateIdentity
from app.models.cv import Candidate, CandidateApplication, CVSource
from app.models.interviews import InterviewInvitation, InterviewSession
from app.models.jd import JDConversation, JobDescription
from app.models.user import User
from app.services.cv.sheet_import import parse_response_rows, response_columns
from app.services.interviews import identification as service, face_matching
from app.services.interviews import sessions
from app.services.interviews.invitations import token_hash


class ResponseColumnTests(unittest.TestCase):
    def sheet(self, image="https://drive.google.com/open?id=photo123"):
        return {"headers": ["Name", "Email", "CV File", "Image", "Salary"], "rows": [
            {"row_number": 2, "values": ["Test Person", "test@example.com",
             "https://drive.google.com/file/d/cv123/view", image, "100"]}]}

    def test_fixed_fields_import_photo_and_ignore_extra_answers(self):
        rows, errors = parse_response_rows(self.sheet())
        self.assertFalse(errors)
        self.assertEqual(rows[0]["photo_file_id"], "photo123")
        self.assertEqual(rows[0]["drive_file_ids"], ["cv123"])
        self.assertNotIn("Salary", rows[0])

    def test_headers_ignore_case_spaces_and_column_order(self):
        self.assertEqual(response_columns([" image ", " NAME ", "email", "CV File", "Extra"]),
                         {"Name": 1, "Email": 2, "CV File": 3, "Image": 0})

    def test_missing_renamed_or_duplicate_required_columns_fail(self):
        for headers in (["Name", "Email", "CV File"], ["Full name", "Email", "CV File", "Image"],
                        ["Name", "Email", "CV File", "Image", "IMAGE"]):
            with self.subTest(headers=headers), self.assertRaises(ValueError):
                response_columns(headers)

    def test_missing_invalid_or_multiple_images_reject_the_response(self):
        for value in ("", "https://example.com/image.jpg", "https://drive.google.com/open?id=one, https://drive.google.com/open?id=two"):
            with self.subTest(image=value):
                rows, errors = parse_response_rows(self.sheet(value))
                self.assertFalse(rows)
                self.assertEqual(errors, [{"row_number": 2, "error_code": "INVALID_RESPONSE_ROW"}])

    def test_optional_timestamp_is_preserved(self):
        sheet = self.sheet()
        sheet["headers"].append("Timestamp")
        sheet["rows"][0]["values"].append("2026-10-22")
        self.assertEqual(parse_response_rows(sheet)[0][0]["submitted_at_text"], "2026-10-22")


class ImageTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("RUN_F7_MODEL_TESTS") == "1", "Set RUN_F7_MODEL_TESTS=1 with models installed")
    def test_installed_models_load_and_reject_a_faceless_image(self):
        output = BytesIO()
        Image.new("RGB", (320, 320), (120, 120, 120)).save(output, "JPEG")
        self.assertEqual(face_matching.count_faces(output.getvalue()), 0)
        with patch.object(face_matching.get_settings(), "face_match_threshold", .363):
            with self.assertRaisesRegex(face_matching.FaceInputError, "reference_unusable"):
                face_matching.compare_faces(output.getvalue(), output.getvalue())

    def test_rejects_empty_invalid_and_oversized_images(self):
        for data in (b"", b"not an image", b"x" * (face_matching.MAX_IMAGE_BYTES + 1)):
            with self.assertRaises(face_matching.FaceInputError):
                face_matching.decode_image(data)

    def test_decoding_bounds_resolution_and_rejects_gif(self):
        output = BytesIO()
        Image.new("RGB", (2000, 1000)).save(output, "PNG")
        self.assertEqual(face_matching.decode_image(output.getvalue()).size, (1280, 640))
        output = BytesIO()
        Image.new("RGB", (100, 100)).save(output, "GIF")
        with self.assertRaises(face_matching.FaceInputError):
            face_matching.decode_image(output.getvalue())

    def test_deletion_clears_every_sensitive_result_field(self):
        row = SimpleNamespace(reference_file_id="photo", similarity=.8, review_note="Private note")
        service.clear_identity(row, "withdrawn")
        self.assertIsNone(row.reference_file_id)
        self.assertIsNone(row.similarity)
        self.assertIsNone(row.review_note)
        self.assertIsNone(row.model_version)
        self.assertEqual(row.consent, "withdrawn")


class AdmissionTests(unittest.TestCase):
    def test_admission_requires_current_invitation_and_prior_verification(self):
        admitted_at = service.now()
        invitation = SimpleNamespace(token_hash="current")
        session = SimpleNamespace(status="in_progress", candidate_admitted_at=admitted_at,
                                  admitted_invitation_hash="previous")
        row = SimpleNamespace(consent="accepted", consent_version=service.CONSENT_VERSION,
                              checked_at=admitted_at - timedelta(seconds=1), status="matched", reviewed_at=None, attempts=0)
        self.assertFalse(service.is_admitted(session, invitation, row))
        session.admitted_invitation_hash = "current"
        self.assertTrue(service.is_admitted(session, invitation, row))
        row.consent_version = "face-verification-v1"
        self.assertFalse(service.is_admitted(session, invitation, row))
        row.consent_version = service.CONSENT_VERSION
        row.checked_at = admitted_at + timedelta(seconds=1)
        self.assertFalse(service.is_admitted(session, invitation, row))
        session.candidate_admitted_at = row.checked_at + timedelta(seconds=1)
        self.assertTrue(service.is_admitted(session, invitation, row))
        session.status = "completed"
        self.assertFalse(service.is_admitted(session, invitation, row))


class RouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = FastAPI()
        self.app.include_router(router)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.token = secrets.token_urlsafe(32)

    async def test_candidate_status_is_private_and_token_is_in_body(self):
        with patch.object(service, "candidate_identity", AsyncMock(return_value={"status": "not_performed"})) as call:
            response = await self.client.post("/interviews/candidate/identity", json={"token": self.token})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            call.assert_awaited_once_with(self.token)

    async def test_frame_body_is_bounded_and_invalid_base64_never_calls_service(self):
        with patch.object(service, "verify_frame", AsyncMock()) as call:
            for frame in ("!invalid!", ""):
                response = await self.client.post("/interviews/candidate/identity/verify", json={"token": self.token, "frame": frame})
                self.assertIn(response.status_code, (413, 422))
            response = await self.client.post("/interviews/candidate/identity/verify", content=b"x" * (7 * 1024 * 1024 + 1))
            self.assertEqual(response.status_code, 413)
            call.assert_not_awaited()

    async def test_valid_frame_contract(self):
        with patch.object(service, "verify_frame", AsyncMock(return_value={"status": "matched"})) as call:
            response = await self.client.post("/interviews/candidate/identity/verify", json={
                "token": self.token, "frame": base64.b64encode(b"frame").decode()})
            self.assertEqual(response.status_code, 200)
            call.assert_awaited_once_with(self.token, b"frame")

    async def test_preview_frame_contract_is_private_and_bounded(self):
        with patch.object(service, "check_frame", AsyncMock(return_value={"face_count": 2, "reason": "multiple_faces"})) as call:
            path = "/interviews/candidate/identity/check-frame"
            response = await self.client.post(path, json={"token": self.token, "frame": base64.b64encode(b"frame").decode()})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json()["face_count"], 2)
            call.assert_awaited_once_with(self.token, b"frame")
            call.reset_mock()
            for frame in ("", "!invalid!"):
                response = await self.client.post(path, json={"token": self.token, "frame": frame})
                self.assertIn(response.status_code, (413, 422))
            response = await self.client.post(path, content=b"x" * (7 * 1024 * 1024 + 1))
            self.assertEqual(response.status_code, 413)
            call.assert_not_awaited()

    async def test_recruiter_endpoints_require_authentication(self):
        path = f"/interviews/jobs/{uuid4()}/applications/{uuid4()}/identity"
        for method, suffix in (("GET", ""), ("POST", "/review"), ("POST", "/delete")):
            response = await self.client.request(method, path + suffix, json={"note": "Reviewed"})
            self.assertEqual(response.status_code, 401)

    async def test_recruiter_service_uses_authenticated_owner(self):
        owner, job, application = uuid4(), uuid4(), uuid4()
        self.app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=owner)
        with patch.object(service, "recruiter_identity", AsyncMock(return_value={"status": "not_performed"})) as call:
            response = await self.client.post(f"/interviews/jobs/{job}/applications/{application}/identity/review", json={"note": " Checked manually "})
            self.assertEqual(response.status_code, 200)
            call.assert_awaited_once_with(job, application, owner, review_note="Checked manually")


@unittest.skipUnless(os.environ.get("RUN_F7_DB_TESTS") == "1", "Set RUN_F7_DB_TESTS=1")
class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.owner, self.application, self.job = uuid4(), uuid4(), uuid4()
        self.token = secrets.token_urlsafe(32)
        self.addAsyncCleanup(self.cleanup)
        async with SessionFactory() as db:
            db.add(User(id=self.owner, email=f"f7-{self.owner}@example.invalid", password_hash="synthetic", role="recruiter"))
            await db.flush()
            conversation = JDConversation(recruiter_id=self.owner)
            db.add(conversation); await db.flush()
            db.add(JobDescription(id=self.job, conversation_id=conversation.id, title="F7 test", content="Synthetic", requirements="Synthetic"))
            candidate = Candidate(recruiter_id=self.owner, full_name="F7 Synthetic", email="f7@example.com")
            db.add(candidate); await db.flush()
            source = CVSource(job_description_id=self.job, drive_folder_id=f"f7-{uuid4()}")
            db.add(source); await db.flush()
            self.source_id = source.id
            db.add(CandidateApplication(id=self.application, candidate_id=candidate.id, job_description_id=self.job, source_id=source.id, source_row_number=2))
            await db.flush()
            db.add(InterviewInvitation(recruiter_id=self.owner, job_description_id=self.job, application_id=self.application,
                recipient_email=candidate.email, token_hash=token_hash(self.token), expires_at=service.now() + timedelta(days=1)))
            await service.link_reference(db, self.application, "syntheticPhoto")
            await db.commit()

    async def cleanup(self):
        async with SessionFactory() as db:
            await db.execute(delete(User).where(User.id == self.owner))
            await db.commit()
        await engine.dispose()

    async def test_preview_checks_require_consent_and_do_not_consume_attempts(self):
        with patch.object(face_matching, "count_faces", return_value=2) as count:
            with self.assertRaises(HTTPException):
                await service.check_frame(self.token, b"frame")
            count.assert_not_called()
            await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
            async with SessionFactory() as db:
                row = await db.get(CandidateIdentity, self.application)
                row.attempts = 4
                await db.commit()
            for faces, reason in ((2, "multiple_faces"), (0, "no_face"), (1, None)):
                count.return_value = faces
                self.assertEqual(await service.check_frame(self.token, b"frame"), {"face_count": faces, "reason": reason})
            async with SessionFactory() as db:
                row = await db.get(CandidateIdentity, self.application)
                self.assertEqual(row.attempts, 4)
                self.assertIsNone(row.checked_at)
                self.assertEqual(row.status, "not_performed")

    async def test_successful_fifth_attempt_remains_valid_but_cannot_be_retaken(self):
        await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            row.attempts = 4
            await db.commit()
        with patch.object(face_matching, "available", return_value=True), \
             patch.object(face_matching, "download_reference", return_value=b"reference"), \
             patch.object(face_matching, "compare_faces", return_value={"status": "matched", "similarity": .82}):
            result = await service.verify_frame(self.token, b"frame")
            self.assertEqual(result["status"], "matched")
            self.assertTrue(result["attempt_limit_reached"])
            self.assertFalse(result["manual_review_required"])
            self.assertTrue(result["admission_ready"])
            with self.assertRaises(HTTPException) as blocked:
                await service.verify_frame(self.token, b"frame")
            self.assertEqual(blocked.exception.status_code, 429)
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            self.assertEqual(row.attempts, 5)

    async def test_preview_and_comparison_stop_at_five_and_for_legacy_higher_counts(self):
        await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            row.attempts = 5
            await db.commit()
        with patch.object(face_matching, "available", return_value=True), patch.object(face_matching, "count_faces") as count:
            for action in (service.check_frame, service.verify_frame):
                with self.assertRaises(HTTPException) as limit:
                    await action(self.token, b"frame")
                self.assertEqual(limit.exception.status_code, 429)
                self.assertIn("5-attempt", limit.exception.detail)
            count.assert_not_called()
            async with SessionFactory() as db:
                row = await db.get(CandidateIdentity, self.application)
                row.attempts = 50
                await db.commit()
            self.assertTrue((await service.candidate_identity(self.token))["attempt_limit_reached"])
            with self.assertRaises(HTTPException) as legacy:
                await service.verify_frame(self.token, b"frame")
            self.assertEqual(legacy.exception.status_code, 429)

    async def test_failed_fifth_attempt_requires_manual_review_before_admission(self):
        await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            row.attempts = 4
            db.add(InterviewSession(application_id=self.application, job_description_id=self.job,
                recruiter_id=self.owner, room_name=f"f7-{uuid4()}", status="in_progress"))
            await db.commit()
        with patch.object(face_matching, "available", return_value=True), \
             patch.object(face_matching, "download_reference", return_value=b"reference"), \
             patch.object(face_matching, "compare_faces", side_effect=face_matching.FaceInputError("multiple_faces")):
            candidate = await service.verify_frame(self.token, b"frame")
        for result in (candidate, await service.candidate_identity(self.token),
                       await service.recruiter_identity(self.job, self.application, self.owner)):
            self.assertEqual(result["attempts"], 5)
            self.assertEqual(result["attempt_limit"], 5)
            self.assertTrue(result["attempt_limit_reached"])
            self.assertTrue(result["manual_review_required"])
            self.assertFalse(result["admission_ready"])
        with self.assertRaises(HTTPException) as pending:
            await sessions.admit_candidate(self.job, self.application, self.owner)
        self.assertEqual(pending.exception.status_code, 409)
        # Changing consent cannot bypass the exhausted-attempt manual review.
        await service.set_consent(self.token, "declined", service.CONSENT_VERSION)
        self.assertFalse((await service.candidate_identity(self.token))["admission_ready"])
        reviewed = await service.recruiter_identity(self.job, self.application, self.owner, review_note="Reviewed application details")
        self.assertTrue(reviewed["attempt_limit_reached"])
        self.assertFalse(reviewed["manual_review_required"])
        self.assertTrue(reviewed["admission_ready"])
        with patch("app.services.interviews.live_transcription.start_transcription"):
            self.assertTrue((await sessions.admit_candidate(self.job, self.application, self.owner))["candidate_admitted"])

    async def test_consent_matching_review_withdrawal_and_reimport(self):
        with patch.object(face_matching, "available", return_value=True), \
             patch.object(face_matching, "download_reference", return_value=b"reference") as download, \
             patch.object(face_matching, "compare_faces", return_value={"status": "matched", "similarity": .8, "threshold": .4, "model_version": "synthetic"}):
            with self.assertRaises(HTTPException):
                await service.verify_frame(self.token, b"frame")
            download.assert_not_called()
            with self.assertRaises(HTTPException):
                await service.set_consent(self.token, "accepted", "old-version")
            await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
            self.assertEqual((await service.verify_frame(self.token, b"frame"))["status"], "matched")
            reviewed = await service.recruiter_identity(self.job, self.application, self.owner, review_note="Checked manually")
            self.assertIsNotNone(reviewed["reviewed_at"])
            with self.assertRaises(HTTPException) as denied:
                await service.recruiter_identity(self.job, self.application, uuid4())
            self.assertEqual(denied.exception.status_code, 404)
            await service.set_consent(self.token, "withdrawn", service.CONSENT_VERSION)
            async with SessionFactory() as db:
                await service.link_reference(db, self.application, "syntheticPhoto")
                await db.commit()
            status = await service.candidate_identity(self.token)
            self.assertFalse(status["has_reference"])
            self.assertEqual(status["consent"], "withdrawn")

    async def test_expiry_purge_removes_results_and_prevents_reimport(self):
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            row.expires_at = service.now() - timedelta(seconds=1)
            row.status, row.similarity, row.review_note = "matched", .9, "Private"
            await db.commit()
        await service.purge_expired()
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            self.assertIsNone(row.reference_file_id)
            self.assertIsNone(row.similarity)
            self.assertIsNone(row.review_note)
            self.assertEqual(row.consent, "expired")

    async def test_invitation_reissue_resets_match_and_consent(self):
        await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            row.status, row.similarity, row.review_note = "matched", .9, "Old review"
            await service.reset_for_invitation(db, self.application)
            await db.commit()
        status = await service.candidate_identity(self.token)
        self.assertEqual(status["consent"], "pending")
        self.assertEqual(status["status"], "not_performed")
        self.assertTrue(status["has_reference"])

    async def test_sheet_connection_requires_fixed_image_column_and_has_no_mapping_preview(self):
        app = FastAPI()
        app.include_router(cv_router)
        app.dependency_overrides[require_recruiter] = lambda: SimpleNamespace(id=self.owner)
        sheet = {"headers": ["Name", "Email", "CV File", "Image", "Salary"], "rows": []}
        base = f"/cv/sources/{self.source_id}/sheet"
        payload = {"spreadsheet_id": "syntheticSheet", "sheet_tab_id": 0}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            self.assertEqual((await client.post(base + "/preview", json=payload)).status_code, 404)
            with patch("app.cv_routes.read_response_sheet", return_value=sheet):
                saved = await client.put(base, json=payload)
                self.assertEqual(saved.status_code, 200)
                self.assertNotIn("field_mapping", saved.json())
                sheet["headers"].remove("Image")
                missing = await client.put(base, json=payload)
                self.assertEqual(missing.status_code, 422)
                self.assertIn("Image", missing.json()["detail"])

    async def test_manual_review_without_photo_does_not_enroll_or_match(self):
        async with SessionFactory() as db:
            await db.execute(delete(CandidateIdentity).where(CandidateIdentity.application_id == self.application))
            await db.commit()
        result = await service.recruiter_identity(self.job, self.application, self.owner, review_note="Reviewed application manually")
        self.assertFalse(result["has_reference"])
        self.assertEqual(result["status"], "not_performed")
        self.assertIsNotNone(result["reviewed_at"])
        await service.recruiter_identity(self.job, self.application, self.owner, delete=True)
        async with SessionFactory() as db:
            await service.link_reference(db, self.application, "syntheticPhoto")
            await db.commit()
        self.assertFalse((await service.candidate_identity(self.token))["has_reference"])

    async def test_bad_frame_retry_limit_and_admission(self):
        await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
        with patch.object(face_matching, "available", return_value=True), \
             patch.object(face_matching, "download_reference", return_value=b"reference"), \
             patch.object(face_matching, "compare_faces", side_effect=face_matching.FaceInputError("multiple_faces")):
            result = await service.verify_frame(self.token, b"frame")
            self.assertEqual(result["reason"], "multiple_faces")
            async with SessionFactory() as db:
                row = await db.get(CandidateIdentity, self.application)
                row.attempts = service.MAX_VERIFICATION_ATTEMPTS
                await db.commit()
            with self.assertRaises(HTTPException) as limited:
                await service.verify_frame(self.token, b"frame")
            self.assertEqual(limited.exception.status_code, 429)
            async with SessionFactory() as db:
                row = await db.get(CandidateIdentity, self.application)
                row.attempts = 1
                db.add(InterviewSession(application_id=self.application, job_description_id=self.job,
                    recruiter_id=self.owner, room_name=f"f7-{uuid4()}", status="in_progress",
                    candidate_admitted_at=service.now(), admitted_invitation_hash=token_hash(self.token)))
                await db.commit()
            with self.assertRaises(HTTPException) as admitted:
                await service.verify_frame(self.token, b"frame")
            self.assertEqual(admitted.exception.status_code, 409)

    async def test_admission_waits_for_comparison_and_score_is_recruiter_only(self):
        async with SessionFactory() as db:
            db.add(InterviewSession(application_id=self.application, job_description_id=self.job,
                recruiter_id=self.owner, room_name=f"f7-{uuid4()}", status="in_progress"))
            await db.commit()
        with self.assertRaises(HTTPException) as pending:
            await sessions.admit_candidate(self.job, self.application, self.owner)
        self.assertEqual(pending.exception.status_code, 409)
        await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
        with self.assertRaises(HTTPException):
            await sessions.admit_candidate(self.job, self.application, self.owner)
        with patch.object(face_matching, "available", return_value=True), \
             patch.object(face_matching, "download_reference", return_value=b"reference"), \
             patch.object(face_matching, "compare_faces", return_value={"status": "not_identified", "similarity": .25,
                 "threshold": .4, "model_version": "synthetic"}):
            candidate = await service.verify_frame(self.token, b"frame")
        self.assertNotIn("similarity", candidate)
        recruiter = await service.recruiter_identity(self.job, self.application, self.owner)
        self.assertEqual(recruiter["similarity"], .25)
        self.assertTrue(recruiter["admission_ready"])
        with patch("app.services.interviews.live_transcription.start_transcription"):
            admitted = await sessions.admit_candidate(self.job, self.application, self.owner)
        self.assertTrue(admitted["candidate_admitted"])

    async def test_previous_consent_requires_updated_capture_explanation(self):
        async with SessionFactory() as db:
            row = await db.get(CandidateIdentity, self.application)
            row.consent = "accepted"
            row.consent_version = "face-verification-v1"
            await db.commit()
        with self.assertRaises(HTTPException):
            await service.verify_frame(self.token, b"frame")
        status = await service.candidate_identity(self.token)
        self.assertEqual(status["consent"], "pending")
        self.assertFalse(status["admission_ready"])

    async def test_stale_admission_allows_capture_but_requires_new_recruiter_permission(self):
        for previous_hash in ("previous-invitation", token_hash(self.token)):
            with self.subTest(previous_hash=previous_hash):
                async with SessionFactory() as db:
                    await db.execute(delete(InterviewSession).where(InterviewSession.application_id == self.application))
                    await service.reset_for_invitation(db, self.application)
                    db.add(InterviewSession(application_id=self.application, job_description_id=self.job,
                        recruiter_id=self.owner, room_name=f"f7-{uuid4()}", status="in_progress",
                        candidate_admitted_at=service.now() - timedelta(days=1), admitted_invitation_hash=previous_hash))
                    await db.commit()
                self.assertFalse((await sessions.candidate_status(self.token))["can_join"])
                await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
                with patch.object(face_matching, "available", return_value=True), \
                     patch.object(face_matching, "download_reference", return_value=b"reference"), \
                     patch.object(face_matching, "compare_faces", return_value={"status": "matched", "similarity": .82,
                         "threshold": .4, "model_version": "synthetic"}):
                    await service.verify_frame(self.token, b"frame")
                self.assertEqual((await service.recruiter_identity(self.job, self.application, self.owner))["similarity"], .82)
                self.assertFalse((await sessions.candidate_status(self.token))["can_join"])
                with self.assertRaises(HTTPException) as waiting:
                    await sessions.candidate_join(self.token)
                self.assertEqual(waiting.exception.status_code, 403)
                with patch("app.services.interviews.live_transcription.start_transcription"):
                    await sessions.admit_candidate(self.job, self.application, self.owner)
                self.assertTrue((await sessions.candidate_status(self.token))["can_join"])
                with self.assertRaises(HTTPException) as admitted:
                    await service.set_consent(self.token, "accepted", service.CONSENT_VERSION)
                self.assertEqual(admitted.exception.status_code, 409)

    async def test_withdrawal_does_not_reactivate_historical_admission(self):
        async with SessionFactory() as db:
            db.add(InterviewSession(application_id=self.application, job_description_id=self.job,
                recruiter_id=self.owner, room_name=f"f7-{uuid4()}", status="in_progress",
                candidate_admitted_at=service.now() - timedelta(days=1),
                admitted_invitation_hash=token_hash(self.token)))
            await db.commit()
        await service.set_consent(self.token, "withdrawn", service.CONSENT_VERSION)
        self.assertFalse((await sessions.candidate_status(self.token))["can_join"])
        with patch("app.services.interviews.live_transcription.start_transcription"):
            await sessions.admit_candidate(self.job, self.application, self.owner)
        self.assertTrue((await sessions.candidate_status(self.token))["can_join"])


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    unittest.main()
