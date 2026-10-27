import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.services.jd.conversations import act_on_jd_conversation, load_jd_conversation, workflow_config
from app.workflows.jd.graph import build_jd_graph
from test_jd_profiles import profile


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_workflow_can_cancel_or_retry_without_new_chat(self):
        for action in ("cancel", "revise"):
            with self.subTest(action=action):
                recruiter_id, conversation_id = uuid4(), uuid4()
                config = workflow_config(conversation_id, recruiter_id)
                conversation = SimpleNamespace(id=conversation_id, created_at=None)
                db = SimpleNamespace(commit=AsyncMock())
                with patch("app.workflows.jd.graph.gather_requirements", return_value={
                    "requirements": "Developer", "screening_profile": profile(), "profile_version": 1,
                    "requirements_ready": True, "status": "drafting"}), \
                     patch("app.workflows.jd.nodes._draft_result", side_effect=[
                         {"status": "failed", "error_code": "JD_EMPTY_RESPONSE"},
                         {"draft_jd": "# Developer", "status": "awaiting_review"},
                     ]), \
                     patch("app.services.jd.conversations.get_owned_conversation", new=AsyncMock(return_value=conversation)):
                    graph = build_jd_graph(InMemorySaver())
                    graph.invoke({"messages": [HumanMessage(content="Create Developer JD")]}, config)
                    snapshot = graph.get_state(config)
                    result = await act_on_jd_conversation(db, graph, recruiter_id, conversation_id,
                        checkpoint_id=snapshot.config["configurable"]["checkpoint_id"], interrupt_id="workflow-recovery",
                        action=action, message="Try again" if action == "revise" else "Cancel")
                    self.assertEqual(result["status"], "cancelled" if action == "cancel" else "awaiting_review")
                    self.assertIsNone(result["error_code"])

    async def test_reload_preserves_drafts_in_revision_order_without_duplicates(self):
        await self.check_reload(legacy=False)

    async def test_old_checkpoint_drafts_are_recovered_on_reload(self):
        await self.check_reload(legacy=True)

    async def check_reload(self, legacy):
        recruiter_id, conversation_id = uuid4(), uuid4()
        conversation = SimpleNamespace(id=conversation_id, created_at=None)
        config = workflow_config(conversation_id, recruiter_id)
        db = SimpleNamespace(scalar=AsyncMock(return_value=None))
        def gather(s):
            return {"requirements": "Developer", "screening_profile": profile(),
                    "profile_version": 1, "requirements_ready": True, "status": "drafting"}
        from app.workflows.jd.nodes import generate_jd
        def generate(s):
            result = generate_jd(s)
            if legacy:
                result.pop("messages", None)
            return result
        with patch("app.workflows.jd.graph.gather_requirements", side_effect=gather), \
             patch("app.workflows.jd.graph.generate_jd", side_effect=generate), \
             patch("app.workflows.jd.nodes._draft_result", side_effect=[
                 {"draft_jd": "# Original Developer", "status": "awaiting_review"},
                 {"draft_jd": "# Revised Developer", "status": "awaiting_review"},
             ]), \
             patch("app.services.jd.conversations.get_owned_conversation", new=AsyncMock(return_value=conversation)):
            graph = build_jd_graph(InMemorySaver())
            graph.invoke({"messages": [HumanMessage(content="Create a developer JD")]}, config)
            graph.invoke(Command(resume={"action": "revise", "feedback": "Change the title"}), config)
            for _ in range(2):
                result = await load_jd_conversation(db, graph, recruiter_id, conversation_id)
                timeline = result["messages"]
                self.assertEqual([m["draft_revision"] for m in timeline if "draft_revision" in m], [0, 1])
                self.assertEqual([m["role"] for m in timeline], ["user", "assistant", "user", "assistant"])
                self.assertIn("Original Developer", timeline[1]["content"])
                self.assertIn("Revised Developer", timeline[3]["content"])
                self.assertEqual(result["status"], "awaiting_review")


if __name__ == "__main__":
    unittest.main()
