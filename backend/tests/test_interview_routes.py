"""Check HTTP validation and real auth dependencies with a fake user store."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException

from app.core.security import create_access_token
from app.db.session import get_db
from app.interview_routes import router


class InterviewRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.job_id = uuid4()
        self.user = SimpleNamespace(id=uuid4(), role="recruiter", is_active=True)
        self.db = SimpleNamespace(get=AsyncMock(return_value=self.user))
        self.app = FastAPI()
        self.app.include_router(router)

        async def test_db():
            yield self.db

        self.app.dependency_overrides[get_db] = test_db
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test"
        )
        self.url = f"/interviews/jobs/{self.job_id}/shortlist/preview"
        self.headers = {"Authorization": f"Bearer {create_access_token(self.user.id)}"}
        self.filters = {"mode": "both", "k": 2, "minimum_score": 80}
        self.preview = AsyncMock(return_value={"proposed": [], "proposed_count": 0})
        self.service_patch = patch("app.interview_routes.preview_shortlist", self.preview)
        self.service_patch.start()
        self.addCleanup(self.service_patch.stop)

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_valid_request_passes_authenticated_identity_and_filters(self):
        response = await self.client.post(self.url, headers=self.headers, json=self.filters)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"proposed": [], "proposed_count": 0})
        self.preview.assert_awaited_once()
        job_id, recruiter_id, filters = self.preview.await_args.args
        self.assertEqual(job_id, self.job_id)
        self.assertEqual(recruiter_id, self.user.id)
        self.assertEqual(filters.mode, "both")
        self.assertEqual(filters.k, 2)
        self.assertEqual(filters.minimum_score, 80)

    async def test_missing_login_is_rejected(self):
        response = await self.client.post(self.url, json=self.filters)
        self.assertEqual(response.status_code, 401)
        self.preview.assert_not_awaited()

    async def test_invalid_token_is_rejected(self):
        response = await self.client.post(
            self.url, headers={"Authorization": "Bearer invalid"}, json=self.filters
        )
        self.assertEqual(response.status_code, 401)
        self.preview.assert_not_awaited()

    async def test_inactive_user_is_rejected(self):
        self.user.is_active = False
        response = await self.client.post(self.url, headers=self.headers, json=self.filters)
        self.assertEqual(response.status_code, 401)
        self.preview.assert_not_awaited()

    async def test_interviewer_is_rejected(self):
        self.user.role = "interviewer"
        response = await self.client.post(self.url, headers=self.headers, json=self.filters)
        self.assertEqual(response.status_code, 403)
        self.preview.assert_not_awaited()

    async def test_invalid_job_id_or_filters_are_rejected(self):
        cases = [
            ("/interviews/jobs/invalid/shortlist/preview", self.filters),
            (self.url, {"mode": "top_k", "k": 0}),
            (self.url, {"mode": "both", "k": 2}),
            (self.url, {"mode": "minimum_score", "minimum_score": 101}),
        ]
        for url, filters in cases:
            with self.subTest(url=url, filters=filters):
                response = await self.client.post(url, headers=self.headers, json=filters)
                self.assertEqual(response.status_code, 422)
        self.preview.assert_not_awaited()

    async def test_service_ownership_rejection_reaches_client(self):
        self.preview.side_effect = HTTPException(404, "Job not found.")
        response = await self.client.post(self.url, headers=self.headers, json=self.filters)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "Job not found."})


if __name__ == "__main__":
    unittest.main()
