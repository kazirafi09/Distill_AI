"""Opt-in local PostgreSQL persistence/retry checks; fixture rows are rolled back."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from langchain_core.messages import HumanMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.core.config import get_settings
from app.models.user import User
from app.models.jd import JDConversation, JobDescription
from app.models.assistant import AssistantConversation
from app.services.assistant.conversations import list_assistant_conversations
from app.services.jd.conversations import (
    act_on_jd_conversation, get_owned_conversation, save_approved_jd, workflow_config,
)
from app.workflows.jd.graph import build_jd_graph
from app.workflows.runtime import open_workflow_checkpointer
from app.services.jd.screening_profiles import public_profile_section
from test_jd_profiles import state


@unittest.skipUnless(os.environ.get("RUN_JD_DB_TESTS") == "1", "Set RUN_JD_DB_TESTS=1 for local PostgreSQL checks")
class PostgreSQLTests(unittest.IsolatedAsyncioTestCase):
    async def test_profile_persistence_retry_ownership_and_legacy_nulls(self):
        engine = create_async_engine(get_settings().database_url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                factory = async_sessionmaker(connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
                async with factory() as db:
                    recruiter = User(email=f"jd-stage-{uuid4()}@example.invalid", password_hash="test-only", role="recruiter")
                    db.add(recruiter)
                    await db.flush()
                    conversation = JDConversation(recruiter_id=recruiter.id)
                    legacy_conversation = JDConversation(recruiter_id=recruiter.id)
                    db.add_all([conversation, legacy_conversation])
                    await db.flush()
                    s = state()
                    s.update(status="approved", approved_jd=s["draft_jd"])
                    document = await save_approved_jd(db, conversation, s)
                    document_id = document.id
                    await db.commit()
                    db.expire(document)
                    saved = await db.scalar(select(JobDescription).where(JobDescription.id == document_id))
                    self.assertEqual(saved.screening_profile, s["screening_profile"])
                    self.assertEqual(saved.profile_version, 1)
                    self.assertEqual(saved.google_form_url, "https://forms.gle/abc123")
                    recovered = await save_approved_jd(db, conversation, s)
                    self.assertEqual(recovered.id, document_id)
                    with self.assertRaises(HTTPException) as denied:
                        await get_owned_conversation(db, uuid4(), conversation.id, lock=True)
                    self.assertEqual(denied.exception.status_code, 404)
                    legacy = JobDescription(conversation_id=legacy_conversation.id, title="Legacy", content="# Legacy", requirements="Old text")
                    db.add(legacy)
                    await db.flush()
                    self.assertIsNone(legacy.screening_profile)
                    self.assertIsNone(legacy.profile_version)
                    self.assertIsNone(legacy.google_form_url)
                    incomplete_conversation = JDConversation(recruiter_id=recruiter.id)
                    db.add(incomplete_conversation)
                    await db.flush()
                    incomplete = state()
                    incomplete["screening_profile"].update(
                        google_form_url=None, weights_confirmed=False,
                        weights_confirmation_quote=None, addressed_dimensions=["technical_skills"],
                    )
                    incomplete["screening_profile"]["criteria"][0].update(priority=None, priority_quote=None)
                    incomplete.update(status="approved", approved_jd=(
                        "# Incomplete Developer\n\n" + public_profile_section(incomplete["screening_profile"])
                    ))
                    incomplete_document = await save_approved_jd(db, incomplete_conversation, incomplete)
                    incomplete_id = incomplete_document.id
                    await db.commit()
                    db.expire(incomplete_document)
                    persisted = await db.get(JobDescription, incomplete_id)
                    self.assertEqual(persisted.screening_profile, incomplete["screening_profile"])
                    self.assertIsNone(persisted.google_form_url)
                    self.assertFalse(persisted.screening_profile["weights_confirmed"])
                    other_recruiter = User(email=f"jd-other-{uuid4()}@example.invalid", password_hash="test-only", role="recruiter")
                    db.add(other_recruiter)
                    await db.flush()
                    own_chat = AssistantConversation(user_id=recruiter.id)
                    other_chat = AssistantConversation(user_id=other_recruiter.id)
                    db.add_all([own_chat, other_chat])
                    await db.flush()
                    graph = SimpleNamespace(get_state=lambda config: SimpleNamespace(values={"messages": [HumanMessage(content="My JD history")]}))
                    listed = await list_assistant_conversations(db, graph, recruiter.id)
                    self.assertEqual([item["conversation_id"] for item in listed["items"]], [str(own_chat.id)])
                    self.assertEqual(listed["items"][0]["title"], "My JD history")
                    empty_page = await list_assistant_conversations(db, graph, recruiter.id, offset=20)
                    self.assertEqual(empty_page["items"], [])
                await transaction.rollback()
        finally:
            await engine.dispose()

    async def test_postgres_checkpoint_approval_retry_after_sql_failure(self):
        conversation_id, recruiter_id = uuid4(), uuid4()
        config = workflow_config(conversation_id, recruiter_id)
        s = state()
        with open_workflow_checkpointer() as saver:
            try:
                with patch("app.workflows.jd.graph.gather_requirements", return_value={**s, "requirements_ready": True}), \
                     patch("app.workflows.jd.nodes._draft_result", return_value={"draft_jd": "# Developer", "status": "awaiting_review"}):
                    graph = build_jd_graph(saver)
                    graph.invoke({"messages": [HumanMessage(content="create")]}, config)
                    snapshot = graph.get_state(config)
                    conversation = SimpleNamespace(id=conversation_id, created_at=None)
                    with patch("app.services.jd.conversations.get_owned_conversation", new=AsyncMock(return_value=conversation)), \
                         patch("app.services.jd.conversations.save_approved_jd", new=AsyncMock(side_effect=RuntimeError("Simulated SQL save interruption"))):
                        with self.assertRaises(RuntimeError):
                            await act_on_jd_conversation(None, graph, recruiter_id, conversation_id,
                                checkpoint_id=snapshot.config["configurable"]["checkpoint_id"],
                                interrupt_id=snapshot.tasks[0].interrupts[0].id, action="approve")
                    # Rebuild from the same PostgreSQL saver, as after a process restart.
                    recovered_graph = build_jd_graph(saver)
                    recovered_state = recovered_graph.get_state(config).values
                    self.assertEqual(recovered_state["status"], "approved")
                    self.assertEqual(recovered_state["screening_profile"], s["screening_profile"])
                    saved = SimpleNamespace(id=uuid4(), title="Developer", number=1, created_at=None)
                    db = SimpleNamespace(commit=AsyncMock())
                    with patch("app.services.jd.conversations.get_owned_conversation", new=AsyncMock(return_value=conversation)), \
                         patch("app.services.jd.conversations.save_approved_jd", new=AsyncMock(return_value=saved)) as persist:
                        response = await act_on_jd_conversation(db, recovered_graph, recruiter_id, conversation_id,
                            checkpoint_id="old", interrupt_id="old", action="approve")
                        self.assertEqual(response["job_description"]["id"], str(saved.id))
                        self.assertEqual(persist.call_args.args[2]["screening_profile"], s["screening_profile"])
            finally:
                saver.delete_thread(config["configurable"]["thread_id"])


if __name__ == "__main__":
    unittest.main()
