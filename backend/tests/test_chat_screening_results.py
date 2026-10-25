"""Saved-result chat reads: bounded output, scoped MCP and no processing actions."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app import mcp_cv as cv, mcp_recruitment as recruitment
from app.core.config import get_settings
from app.integrations import cv_results_mcp as proxy
from app.integrations.assistant_mcp import permitted_tools, create_context_token
from app.models.auth_session import AuthSession
from app.services.cv.result_summary import summarize_results


def candidate(name, *, score="80", status="completed", outdated=False, rank=1, error=None):
    return {"id": str(uuid4()), "application_id": str(uuid4()), "name": name,
        "status": status, "outdated": outdated, "rank": rank, "error_code": error,
        "score": {"aggregate": score, "must_have_review_flags": ["criterion"]} if score is not None else None,
        "warnings": [], "matches": [{"evidence": "private source text"}]}


def saved_results(job_id):
    return {"job_id": str(job_id), "run": {"id": str(uuid4()), "status": "completed_with_issues", "total": 5},
        "ranked": [candidate("Ada", score="90", rank=1), candidate("Sam", score="90", rank=1), candidate("Zero", score="0", rank=3)],
        "incomplete": [candidate("Failed", score=None, status="failed", rank=None, error="CV_PARSING_FAILED"),
                       candidate("Older", score="95", outdated=True)], "warnings": []}


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.result = saved_results(uuid4())

    def test_bounded_results_preserve_ties_zero_and_next_page(self):
        result = summarize_results(self.result, limit=2)
        self.assertEqual([item["rank"] for item in result["candidates"]], [1, 1])
        self.assertTrue(result["has_more"])
        self.assertEqual(result["next_offset"], 2)
        next_page = summarize_results(self.result, offset=2)
        self.assertEqual(next_page["candidates"][0]["score"], "0")
        self.assertFalse(result["screening_started"])
        self.assertNotIn("matches", result["candidates"][0])

    def test_processing_issues_do_not_include_outdated_or_unprocessed_candidates(self):
        self.result["incomplete"].append(candidate("Pending", score=None, status="pending"))
        result = summarize_results(self.result, view="issues")
        self.assertEqual([item["name"] for item in result["candidates"]], ["Failed"])
        self.assertEqual(result["counts"], {"ranked": 3, "unranked": 3, "processing_issues": 1, "outdated": 1})

    def test_older_and_unverified_scores_are_never_current_ranks(self):
        self.result["incomplete"].append(candidate("Unverified", score="99"))
        self.result["warnings"] = ["CURRENT_MODEL_VERSIONS_UNAVAILABLE_RANKING_WITHHELD"]
        result = summarize_results(self.result, view="all", limit=20)
        old, unverified = result["candidates"][-2:]
        for item in (old, unverified):
            self.assertIsNone(item["rank"])
            self.assertIsNone(item["score"])
        self.assertEqual(old["score_status"], "older")
        self.assertEqual(unverified["score_status"], "withheld")
        self.assertEqual(unverified["saved_score"], "99")
        self.assertEqual(result["warnings"], self.result["warnings"])

    def test_no_saved_run_is_distinct_from_empty_rankings(self):
        self.result.update(run=None, ranked=[], incomplete=[])
        result = summarize_results(self.result)
        self.assertEqual(result["status"], "not_started")
        self.assertEqual(result["candidates"], [])

    def test_invalid_page_or_view_is_rejected(self):
        for args in ({"limit": 0}, {"limit": 21}, {"offset": -1}, {"offset": 10001}, {"view": "send"}):
            with self.assertRaises(ValueError):
                summarize_results(self.result, **args)


class ResultsProxyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.owner, self.conversation, self.job, self.sid = uuid4(), uuid4(), uuid4(), uuid4()

    def test_delegation_has_its_own_audience_job_scope_and_short_expiry(self):
        token = proxy.create_results_token(self.owner, self.conversation, self.job, self.sid)
        claims = jwt.decode(token, get_settings().jwt_secret_key.get_secret_value(), algorithms=["HS256"], audience=proxy.RESULTS_AUDIENCE)
        self.assertEqual(claims["exp"] - claims["iat"], 90)
        self.assertEqual(claims["job_id"], str(self.job))
        self.assertEqual(claims["conversation_id"], str(self.conversation))
        self.assertEqual(claims["sid"], str(self.sid))
        self.assertEqual(claims["tool"], "get_screening_results")
        with self.assertRaises(jwt.InvalidAudienceError):
            jwt.decode(token, get_settings().jwt_secret_key.get_secret_value(), algorithms=["HS256"], audience="recruitment-mcp")

    async def test_facade_passes_job_and_owner_but_never_changes_chat_context(self):
        context = recruitment.authorized_context.set(recruitment.RecruitmentContext(
            self.owner, ["get_screening_results"], {}, conversation_id=self.conversation, auth_session_id=self.sid))
        try:
            with patch.object(recruitment.delivery, "resolve_job", new=AsyncMock(return_value=self.job)) as resolve, \
                 patch.object(recruitment, "read_screening_results", new=AsyncMock(return_value=summarize_results(saved_results(self.job)))) as read:
                result = await recruitment.get_screening_results(job_number=139)
                resolve.assert_awaited_once_with(self.owner, 139, None)
                read.assert_awaited_once_with(self.owner, self.conversation, self.job, session_id=self.sid, limit=5, offset=0, view="ranked")
                self.assertEqual(result["job_number"], 139)
                self.assertFalse(result["screening_started"])
                self.assertEqual(recruitment.authorized_context.get().state, {})
        finally:
            recruitment.authorized_context.reset(context)

    async def test_interview_chat_cannot_discover_or_execute_results(self):
        tools = [SimpleNamespace(name="get_screening_results"), SimpleNamespace(name="search_interview_questions")]
        self.assertEqual([tool.name for tool in permitted_tools(tools, {}, {"configurable": {"interview_session_id": "fixture"}})], ["search_interview_questions"])
        context = recruitment.authorized_context.set(recruitment.RecruitmentContext(self.owner, ["get_screening_results"], {}, uuid4()))
        try:
            with self.assertRaises(ValueError):
                await recruitment.get_screening_results(job_number=139)
        finally:
            recruitment.authorized_context.reset(context)

    async def test_scoped_tool_blocks_other_jobs_and_processing_before_service_calls(self):
        job = cv.results_job_context.set(self.job)
        owner = cv.recruiter_context.set(self.owner)
        try:
            with patch("app.services.cv.screening.get_screening_results", new=AsyncMock()) as read:
                with self.assertRaises(ValueError):
                    await cv.get_screening_results(str(uuid4()))
                for function, args in ((cv.parse_cv, (str(uuid4()),)), (cv.get_cv_profile, (str(uuid4()),)),
                    (cv.start_job_screening, (str(self.job),)), (cv.search_cv_evidence, (str(self.job), str(uuid4()), "evidence"))):
                    with self.assertRaises(ValueError):
                        await function(*args)
                read.assert_not_awaited()
        finally:
            cv.results_job_context.reset(job)
            cv.recruiter_context.reset(owner)

    async def test_existing_full_result_contract_is_preserved(self):
        result = saved_results(self.job)
        owner = cv.recruiter_context.set(self.owner)
        try:
            with patch("app.services.cv.screening.get_screening_results", new=AsyncMock(return_value=result)):
                response = await cv.get_screening_results(str(self.job))
            self.assertEqual(response.result, result)
        finally:
            cv.recruiter_context.reset(owner)

    async def test_real_cv_mcp_proxy_uses_scoped_auth_and_filters_discovery(self):
        user = SimpleNamespace(id=self.owner, role="recruiter", is_active=True)
        browser = SimpleNamespace(user_id=self.owner, expires_at=datetime.now(timezone.utc) + timedelta(days=1))
        class DB:
            async def __aenter__(inner): return inner
            async def __aexit__(inner, *args): pass
            async def get(inner, model, identifier): return browser if model is AuthSession else user
        original = httpx.AsyncClient
        def local_http(**kwargs):
            return original(transport=httpx.ASGITransport(app=cv.app), **kwargs)
        async with cv.mcp.session_manager.run():
            with patch.object(cv, "SessionFactory", DB), \
                 patch("app.services.assistant.conversations.get_owned_assistant", new=AsyncMock()), \
                 patch("app.services.cv.screening.get_screening_results", new=AsyncMock(return_value=saved_results(self.job))) as service, \
                 patch.object(proxy.httpx, "AsyncClient", side_effect=local_http), \
                 patch.object(proxy, "get_mcp_servers", return_value={"cv": {"url": "http://localhost:8000/mcp/"}}):
                result = await proxy.read_screening_results(self.owner, self.conversation, self.job, session_id=self.sid, limit=2)
                self.assertEqual(len(result["candidates"]), 2)
                service.assert_awaited_once_with(self.job, self.owner)
                token = proxy.create_results_token(self.owner, self.conversation, self.job, self.sid)
                async with local_http(headers={"Authorization": f"Bearer {token}"}) as http:
                    async with streamable_http_client("http://localhost:8000/mcp/", http_client=http) as (read, write, _):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            self.assertEqual([tool.name for tool in (await session.list_tools()).tools], ["get_screening_results"])
                config = {"configurable": {"thread_id": f"assistant:{self.conversation}", "user_id": str(self.owner)}}
                wrong_audience = create_context_token(config, ["get_screening_results"])
                async with local_http() as http:
                    denied = await http.post("http://localhost:8000/mcp/", headers={"Authorization": f"Bearer {wrong_audience}"}, json={})
                    self.assertEqual(denied.status_code, 401)
                    browser.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    revoked = await http.post("http://localhost:8000/mcp/", headers={"Authorization": f"Bearer {token}"}, json={})
                    self.assertEqual(revoked.status_code, 401)
        with self.assertRaises(LookupError):
            cv.recruiter_context.get()
        self.assertIsNone(cv.results_job_context.get())


if __name__ == "__main__":
    unittest.main()
