"""Auth validation plus opt-in PostgreSQL lifecycle checks, rolled back after each test."""
import os
import asyncio
import sys
import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from uuid import uuid4

import httpx
import jwt
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth import router, SignupRequest
from app.core.config import get_settings
from app.core.security import verify_password
from app.db.session import get_db
from app.models.auth_session import AuthSession
from app.models.user import User

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class SignupValidationTests(unittest.TestCase):
    def test_short_password_and_invalid_email_rejected(self):
        for email, password in [("person@example.com", "short"), ("invalid", "long-password-test")]:
            with self.assertRaises(ValueError):
                SignupRequest(email=email, password=password)


@unittest.skipUnless(os.environ.get("RUN_AUTH_DB_TESTS") == "1", "Set RUN_AUTH_DB_TESTS=1 for PostgreSQL checks")
class AuthLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(get_settings().database_url)
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = async_sessionmaker(self.connection, expire_on_commit=False,
                                    join_transaction_mode="create_savepoint")()
        app = FastAPI()
        app.include_router(router)

        async def db_override():
            yield self.db

        app.dependency_overrides[get_db] = db_override
        self.transport = httpx.ASGITransport(app=app)
        self.origin = get_settings().public_app_url.rstrip("/")
        self.client = httpx.AsyncClient(transport=self.transport, base_url=self.origin,
                                       headers={"Origin": self.origin})
        self.email = f"auth-{uuid4()}@example.com"
        self.password = "test-only-password-123"

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def signup(self):
        response = await self.client.post("/auth/signup", json={
            "email": self.email, "password": self.password, "role": "interviewer",
        })
        self.assertEqual(response.status_code, 201, response.text)
        return response

    async def test_signup_reload_expired_access_and_logout(self):
        response = await self.signup()
        cookie = response.headers["set-cookie"]
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=lax", cookie)
        self.assertIn("Max-Age=604800", cookie)
        if self.origin.startswith("https:"):
            self.assertIn("Secure", cookie)
        self.assertEqual(response.headers["cache-control"], "no-store")
        user = await self.db.scalar(select(User).where(User.email == self.email))
        self.assertEqual(user.role, "recruiter")
        self.assertTrue(verify_password(self.password, user.password_hash))
        secret = self.client.cookies.get("distill_session")
        session = await self.db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
        self.assertEqual(session.token_hash, sha256(secret.encode()).hexdigest())
        token = response.json()["access_token"]
        self.assertEqual((await self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code, 200)

        # A new page starts without an access token and restores from its cookie.
        async with httpx.AsyncClient(transport=self.transport, base_url=self.origin,
                                     cookies=self.client.cookies, headers={"Origin": self.origin}) as reloaded:
            restored = await reloaded.post("/auth/refresh")
            self.assertEqual(restored.status_code, 200)
            expired = jwt.encode({"sub": str(user.id), "sid": str(session.id),
                                  "iat": datetime.now(timezone.utc) - timedelta(hours=1),
                                  "exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
                                 get_settings().jwt_secret_key.get_secret_value(), algorithm="HS256")
            self.assertEqual((await reloaded.get("/auth/me", headers={"Authorization": f"Bearer {expired}"})).status_code, 401)
            renewed = await reloaded.post("/auth/refresh")
            self.assertEqual(renewed.status_code, 200)
            self.assertEqual((await reloaded.get("/auth/recruiter-check", headers={
                "Authorization": f"Bearer {renewed.json()['access_token']}"})).status_code, 200)
            self.assertEqual((await reloaded.post("/auth/logout")).status_code, 204)
            self.assertIsNone(reloaded.cookies.get("distill_session"))
        # Old cookie and access token are both revoked after sign-out.
        self.assertEqual((await self.client.post("/auth/refresh")).status_code, 401)
        self.assertEqual((await self.client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code, 401)

    async def test_duplicate_email_normalization_and_login(self):
        await self.signup()
        duplicate = await self.client.post("/auth/signup", json={"email": self.email.upper(), "password": self.password})
        self.assertEqual(duplicate.status_code, 409)
        bad = await self.client.post("/auth/login", json={"email": self.email, "password": "wrong-password"})
        self.assertEqual(bad.status_code, 401)
        good = await self.client.post("/auth/login", json={"email": self.email.upper(), "password": self.password})
        self.assertEqual(good.status_code, 200)

    async def test_inactive_and_expired_sessions_cannot_restore(self):
        await self.signup()
        user = await self.db.scalar(select(User).where(User.email == self.email))
        user.is_active = False
        await self.db.commit()
        self.assertEqual((await self.client.post("/auth/refresh")).status_code, 401)
        self.assertEqual((await self.client.post("/auth/login", json={"email": self.email, "password": self.password})).status_code, 401)
        user.is_active = True
        session = await self.db.scalar(select(AuthSession).where(AuthSession.user_id == user.id))
        session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await self.db.commit()
        self.assertEqual((await self.client.post("/auth/refresh")).status_code, 401)

    async def test_origin_and_missing_cookie_protection(self):
        self.assertEqual((await self.client.post("/auth/refresh")).status_code, 401)
        await self.signup()
        for path in ("login", "signup", "refresh", "logout"):
            response = await self.client.post(f"/auth/{path}", headers={"Origin": "https://untrusted.example"},
                                              json={"email": self.email, "password": self.password})
            self.assertEqual(response.status_code, 403)
        self.assertEqual((await self.client.post("/auth/refresh")).status_code, 200)
        malformed = await self.client.post("/auth/refresh", headers={"Origin": "https://[invalid"})
        self.assertEqual(malformed.status_code, 403)
        cross_site = await self.client.post("/auth/refresh", headers={"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(cross_site.status_code, 403)

    async def test_local_development_cookie(self):
        async with httpx.AsyncClient(transport=self.transport, base_url="http://127.0.0.1:8000",
                                     headers={"Origin": "http://localhost:5173"}) as local:
            response = await local.post("/auth/signup", json={"email": self.email, "password": self.password})
            self.assertEqual(response.status_code, 201)
            self.assertNotIn("Secure", response.headers["set-cookie"])
            self.assertEqual((await local.post("/auth/refresh")).status_code, 200)


if __name__ == "__main__":
    unittest.main()
