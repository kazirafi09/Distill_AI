"""Chat commands use the same ownership, checkpoint and acceptance checks."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from fastapi import HTTPException

from app.services.assistant.conversations import send_assistant_message
from app.workflows.jd.intent import JDIntent


class MessageTests(unittest.IsolatedAsyncioTestCase):
    async def run_message(self, message, jd, checkpoint="cp", intent=None, intent_error=False):
        db = SimpleNamespace(commit=AsyncMock())
        assistant = SimpleNamespace(id=uuid4())
        graph = SimpleNamespace(
            get_state=Mock(return_value=SimpleNamespace(
                next=(), values={}, config={"configurable": {"checkpoint_id": "cp"}},
            )), update_state=Mock(), invoke=Mock(),
        )
        with patch("app.services.assistant.conversations.get_owned_assistant", new=AsyncMock(return_value=assistant)), \
             patch("app.services.assistant.conversations.interpret_jd_message", side_effect=ValueError("model unavailable") if intent_error else None,
                   return_value=intent or JDIntent(action=("approve" if message in {"save", "accept", "accept and save"} else "cancel" if message == "cancel" else "reply" if jd["status"] == "gathering" else "revise"))), \
             patch("app.services.assistant.conversations.linked_jd", new=AsyncMock(return_value=jd)), \
             patch("app.services.assistant.conversations.send_assistant_jd_action", new=AsyncMock(return_value={"routed": True})) as action, \
             patch("app.services.assistant.conversations.assistant_response", new=AsyncMock(return_value={"prompted": True})):
            result = await send_assistant_message(db, graph, None, uuid4(), assistant.id, message, checkpoint)
        return result, action, graph

    async def test_model_understands_cancel_typo_without_revising(self):
        jd = {"status": "awaiting_review", "conversation_id": "jd", "checkpoint_id": "jdcp", "interrupt": {"id": "pause"}}
        _, action, _ = await self.run_message("Cancle this job description", jd, intent=JDIntent(action="cancel"))
        self.assertEqual(action.call_args.kwargs["action"], "cancel")

    async def test_question_does_not_regenerate_draft(self):
        jd = {"status": "awaiting_review", "conversation_id": "jd"}
        _, action, graph = await self.run_message("Why are criteria empty?", jd,
            intent=JDIntent(action="chat", response="Ask me to suggest criteria from role knowledge."))
        action.assert_not_called()
        self.assertIn("suggest criteria", graph.update_state.call_args.args[1]["messages"][1].content)

    async def test_model_failure_preserves_workflow(self):
        _, action, graph = await self.run_message("Cancle", {"status": "awaiting_review", "conversation_id": "jd"}, intent_error=True)
        action.assert_not_called()
        self.assertIn("unchanged", graph.update_state.call_args.args[1]["messages"][1].content)

    async def test_chat_routes_review_and_gathering_actions(self):
        for status, message, expected in (
            ("awaiting_review", "accept and save", "approve"),
            ("awaiting_review", "cancel", "cancel"),
            ("gathering", "cancel", "cancel"),
            ("gathering", "Python developer", "reply"),
            ("awaiting_review", "Change the title", "revise"),
            ("approved", "save", "approve"),
        ):
            with self.subTest(status=status, message=message):
                jd = {"status": status, "checkpoint_id": "jdcp", "conversation_id": "jd",
                      "acceptance_errors": [], "interrupt": {"id": "pause"}, "job_description": None}
                if status == "approved":
                    jd["interrupt"] = None
                result, action, graph = await self.run_message(message, jd)
                self.assertEqual(result, {"routed": True})
                self.assertEqual(action.call_args.kwargs["action"], expected)
                self.assertEqual(action.call_args.kwargs["message"], message)
                self.assertEqual(action.call_args.kwargs["jd_checkpoint_id"], "jdcp")
                graph.invoke.assert_not_called()

    async def test_blocked_save_prompts_in_chat(self):
        jd = {"status": "awaiting_review", "conversation_id": "jd",
              "acceptance_errors": ["The draft does not reflect the current screening profile version."]}
        result, action, graph = await self.run_message("save", jd)
        self.assertEqual(result, {"prompted": True})
        action.assert_not_called()
        messages = graph.update_state.call_args.args[1]["messages"]
        self.assertEqual(messages[0].content, "save")
        self.assertIn("current screening profile version", messages[1].content)

    async def test_incomplete_requirements_do_not_block_chat_save(self):
        jd = {"status": "awaiting_review", "conversation_id": "jd", "checkpoint_id": "jdcp",
              "interrupt": {"id": "pause"}, "acceptance_errors": [],
              "screening_warnings": ["Confirm weights.", "No application URL."]}
        result, action, graph = await self.run_message("accept and save", jd)
        self.assertEqual(result, {"routed": True})
        self.assertEqual(action.call_args.kwargs["action"], "approve")
        graph.update_state.assert_not_called()

    async def test_stale_chat_checkpoint_is_rejected(self):
        with self.assertRaises(HTTPException) as error:
            await self.run_message("accept", {"status": "awaiting_review"}, checkpoint="old")
        self.assertEqual(error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
