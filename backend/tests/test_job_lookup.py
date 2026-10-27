"""Read-only job lookup: access boundaries, literal search and chat output."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import AIMessage, ToolMessage

from app.services.jd import lookup
from app import mcp_recruitment as server
from app.integrations.assistant_mcp import permitted_tools
from app.workflows.assistant.nodes import assistant_turn


class JobLookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_bad_arguments_never_open_a_database_session(self):
        with patch.object(lookup, "SessionFactory") as factory:
            for arguments in ({"job_number": 0}, {"job_number": 2**63}, {"title": " "},
                {"title": "x" * 201}, {"limit": 11}, {"offset": -1}, {"offset": 1001},
                {"job_number": 1, "title": "Python"}):
                self.assertEqual((await lookup.find_owned_jobs(uuid4(), **arguments))["status"], "invalid_request")
            factory.assert_not_called()

    async def test_tool_uses_authenticated_owner_and_requires_its_credential(self):
        owner = uuid4()
        context = server.authorized_context.set(server.RecruitmentContext(owner, ["find_jobs"], {}))
        try:
            with patch.object(server, "find_owned_jobs", new=AsyncMock(return_value={"status": "not_found"})) as find:
                await server.find_jobs(job_number=139)
                find.assert_awaited_once_with(owner, job_number=139, title=None, limit=5, offset=0)
                server.authorized_context.set(server.RecruitmentContext(owner, [], {}))
                with self.assertRaises(ValueError):
                    await server.find_jobs(job_number=139)
        finally:
            server.authorized_context.reset(context)

    async def test_interview_chat_cannot_discover_or_execute_job_lookup(self):
        tools = [SimpleNamespace(name="find_jobs"), SimpleNamespace(name="search_interview_questions")]
        filtered = permitted_tools(tools, {}, {"configurable": {"interview_session_id": str(uuid4())}})
        self.assertEqual([tool.name for tool in filtered], ["search_interview_questions"])
        context = server.authorized_context.set(server.RecruitmentContext(uuid4(), ["find_jobs"], {}, uuid4()))
        try:
            with patch.object(server, "find_owned_jobs", new=AsyncMock()) as find:
                with self.assertRaises(ValueError):
                    await server.find_jobs(job_number=139)
                find.assert_not_awaited()
        finally:
            server.authorized_context.reset(context)

    def test_lookup_reply_receives_saved_content_without_another_tool_or_navigation(self):
        message = ToolMessage(name="find_jobs", tool_call_id="lookup", content='{"status":"found","jobs":[{"number":139,"content":"Build APIs."}]}')
        settings = SimpleNamespace(gemini_api_key=SimpleNamespace(get_secret_value=lambda: "fixture"))
        with patch("app.workflows.assistant.nodes.get_settings", return_value=settings), \
             patch("app.workflows.assistant.nodes.make_assistant_model") as model, \
             patch("app.workflows.assistant.nodes.discover_assistant_tools") as discovery:
            model.return_value.invoke.return_value = AIMessage(content="JD 139\n\nBuild APIs.")
            result = assistant_turn({"messages": [message]}, {})
            model.assert_called_once_with(settings, max_tokens=6000)
            discovery.assert_not_called()
            prompt = model.return_value.invoke.call_args.args[0]
            self.assertIn("Do not redirect", prompt[0].content)
            self.assertIn("Build APIs.", prompt[-1].content)
            self.assertEqual(result["messages"][0].content, "JD 139\n\nBuild APIs.")


@unittest.skipUnless(os.environ.get("RUN_JOB_LOOKUP_DB_TESTS") == "1", "Opt in to disposable-schema lookup checks")
class JobLookupDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_number_title_ambiguity_literal_wildcards_and_pagination(self):
        from sqlalchemy import select, func
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.pool import NullPool
        from sqlalchemy.schema import CreateSchema, DropSchema
        from app.core.config import get_settings
        from app.db.base import Base
        from app.models.user import User
        from app.models.jd import JDConversation, JobDescription, DiscordJobPost

        engine = create_async_engine(get_settings().database_url, poolclass=NullPool)
        schema = "job_lookup_" + uuid4().hex
        scoped = engine.execution_options(schema_translate_map={None: schema})
        factory = async_sessionmaker(scoped, expire_on_commit=False)
        async with engine.begin() as connection:
            await connection.execute(CreateSchema(schema))
        try:
            async with scoped.begin() as connection:
                await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[model.__table__ for model in (User, JDConversation, JobDescription, DiscordJobPost)]))
            async with factory() as db:
                owner, other = User(email="owner@example.com", password_hash="unused", role="recruiter"), User(email="other@example.com", password_hash="unused", role="recruiter")
                db.add_all([owner, other]); await db.flush()
                docs = []
                for user, title in ((owner, "Python developer"), (owner, "Senior Python developer"),
                                    (owner, "Python % analyst"), (other, "Private Python role")):
                    conversation = JDConversation(recruiter_id=user.id)
                    db.add(conversation); await db.flush()
                    document = JobDescription(conversation_id=conversation.id, title=title, content="# Role\n\nBuild APIs.", requirements="Saved requirements",
                        screening_profile={"criteria": [{"text": "Python", "dimension": "technical_skills", "priority": "Must have"}],
                            "dimension_weights": {"technical_skills": 100}} if len(docs) == 0 else None,
                        profile_version=1 if len(docs) == 0 else None)
                    db.add(document); await db.flush(); docs.append(document)
                await db.commit()
                owner_id, first, private = owner.id, docs[0].number, docs[3].number
            with patch.object(lookup, "SessionFactory", factory):
                result = await lookup.find_owned_jobs(owner_id, job_number=first)
                self.assertEqual(result["status"], "found")
                job = result["jobs"][0]
                self.assertIn("Build APIs.", job["content"])
                self.assertEqual(job["screening_profile"]["dimension_weights"]["technical_skills"], 100)
                self.assertEqual(job["essential_criteria"], 1)
                for field in ("conversation_id", "edit_version", "editable_content", "url"):
                    self.assertNotIn(field, job)
                self.assertEqual((await lookup.find_owned_jobs(owner_id, job_number=private))["status"], "not_found")
                self.assertEqual((await lookup.find_owned_jobs(owner_id, job_number=99999))["status"], "not_found")
                matches = await lookup.find_owned_jobs(owner_id, title="pYtHoN", limit=1)
                self.assertEqual(matches["status"], "matches")
                self.assertTrue(matches["has_more"])
                self.assertNotIn("content", matches["jobs"][0])
                next_page = await lookup.find_owned_jobs(owner_id, title="Python", limit=1, offset=matches["next_offset"])
                self.assertNotEqual(next_page["jobs"][0]["number"], matches["jobs"][0]["number"])
                literal = await lookup.find_owned_jobs(owner_id, title="%")
                self.assertEqual(literal["status"], "found")
                self.assertEqual(literal["jobs"][0]["title"], "Python % analyst")
                self.assertIsNone(literal["jobs"][0]["screening_profile"])
                self.assertIsNone(literal["jobs"][0]["essential_criteria"])
                all_jobs = await lookup.find_owned_jobs(owner_id)
                self.assertEqual(len(all_jobs["jobs"]), 3)
                self.assertTrue(all("content" not in job for job in all_jobs["jobs"]))
            async with factory() as db:
                self.assertEqual(await db.scalar(select(func.count()).select_from(JobDescription)), 4)
        finally:
            async with engine.begin() as connection:
                await connection.execute(DropSchema(schema, cascade=True))
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
