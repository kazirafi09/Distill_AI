"""Real HTTP MCP discovery/execution with mocked persistence and external actions."""

import asyncio
import json
import socket
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import jwt
import uvicorn
from fastapi import FastAPI, HTTPException
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver

from app.core.config import get_settings
from app.integrations import assistant_mcp as client
from app import mcp_recruitment as server
from app.models.auth_session import AuthSession
from app.workflows.assistant.graph import build_assistant_graph
from app.workflows.assistant.nodes import assistant_turn, execute_assistant_tool


class AssistantMCPUnitTests(unittest.TestCase):
    def test_discovery_failure_stops_model_invocation(self):
        settings = SimpleNamespace(gemini_api_key=SimpleNamespace(get_secret_value=lambda: "test"))
        with patch("app.workflows.assistant.nodes.get_settings", return_value=settings), \
             patch("app.workflows.assistant.nodes.discover_assistant_tools", side_effect=OSError()), \
             patch("app.workflows.assistant.nodes.make_assistant_model") as model:
            result = assistant_turn({"messages": [HumanMessage(content="Hello")]}, {})
        self.assertEqual(result["error_code"], "ASSISTANT_MCP_UNAVAILABLE")
        model.assert_not_called()

    def test_lost_response_completes_turn_without_retrying_action(self):
        message = AIMessage(content="", tool_calls=[{
            "id": "confirm", "name": "confirm_interview_invitations", "args": {"approval_message": "Yes"},
        }])
        review = {"batch_id": "saved-preview"}
        with patch("app.workflows.assistant.nodes.call_assistant_tool", side_effect=TimeoutError()) as call:
            result = execute_assistant_tool({"messages": [message], "invitation_review": review}, {})
        call.assert_called_once()
        self.assertEqual(json.loads(result["messages"][0].content)["status"], "uncertain")
        self.assertEqual(result["invitation_review"], review)
        self.assertIsNone(result["error_code"])

    def test_context_credentials_do_not_grant_unrelated_tools(self):
        config = {"configurable": {"thread_id": f"assistant:{uuid4()}", "user_id": str(uuid4()), "auth_session_id": str(uuid4())}}
        token = client.create_context_token(config, ["get_interview_invitation_status"])
        claims = jwt.decode(token, get_settings().jwt_secret_key.get_secret_value(), algorithms=["HS256"], audience="recruitment-mcp")
        self.assertEqual(claims["allowed_tools"], ["get_interview_invitation_status"])
        self.assertEqual(claims["sid"], config["configurable"]["auth_session_id"])
        self.assertLessEqual(claims["exp"] - claims["iat"], 120)


@patch("app.workflows.assistant.nodes.check_scope", new=lambda *_: True)
class AssistantMCPHTTPTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_execution_graph_and_access_guards(self):
        owner, conversation_id, session_id = uuid4(), uuid4(), uuid4()
        graph = build_assistant_graph(InMemorySaver())
        config = {"configurable": {
            "thread_id": f"assistant:{conversation_id}", "user_id": str(owner),
            "auth_session_id": str(session_id),
        }}
        user = SimpleNamespace(id=owner, role="recruiter", is_active=True)
        browser_session = SimpleNamespace(user_id=owner, expires_at=datetime.now(timezone.utc) + timedelta(days=1))

        class DB:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, model, identifier):
                return browser_session if model is AuthSession else user

        @asynccontextmanager
        async def lifespan(app):
            async with server.mcp.session_manager.run():
                yield

        app = FastAPI(lifespan=lifespan)
        app.state.assistant_graph = graph
        app.mount("/mcp/recruitment", server.transport_app)
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        url = f"http://127.0.0.1:{port}/mcp/recruitment/"
        http_server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))

        with patch.object(server, "SessionFactory", DB), \
             patch.object(server, "get_owned_assistant", new_callable=AsyncMock) as owned, \
             patch.object(client, "get_mcp_servers", return_value={"recruitment": {"transport": "http", "url": url}}):
            serving = asyncio.create_task(http_server.serve(sockets=[listener]))
            try:
                async with asyncio.timeout(10):
                    while not http_server.started:
                        if serving.done():
                            await serving
                        await asyncio.sleep(0.01)
                client.configure_mcp_loop(asyncio.get_running_loop())

                # Discovery comes from the server; filtering comes from conversation policy.
                tools = await asyncio.to_thread(client.discover_assistant_tools, {}, config)
                self.assertEqual(len(tools), 6)
                self.assertIn("find_jobs", [tool.name for tool in tools])
                self.assertIn("get_screening_results", [tool.name for tool in tools])
                self.assertNotIn("confirm_interview_invitations", [tool.name for tool in tools])
                self.assertNotIn("send_email", [tool.name for tool in tools])
                self.assertTrue(all("recruiter_id" not in tool.args for tool in tools))
                discovery_tools = await client.load_tools(config, [])
                start_tool = next(tool for tool in discovery_tools if tool.name == "start_jd_creation")
                denied = await start_tool.ainvoke({"type": "tool_call", "id": "denied", "name": start_tool.name, "args": {}})
                self.assertEqual(denied.status, "error")

                # Job lookup travels through MCP and returns data in this chat,
                # without attaching a JD workflow or changing the selected job.
                lookup = {"status": "found", "jobs": [{"number": 139, "title": "Python developer",
                    "content": "Build Python APIs.", "screening_profile": None}], "has_more": False, "next_offset": None}
                with patch.object(server, "find_owned_jobs", new=AsyncMock(return_value=lookup)) as find:
                    with patch("app.workflows.assistant.nodes.make_assistant_model") as model:
                        model.return_value.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
                            "id": "lookup", "name": "find_jobs", "args": {"job_number": 139}}])
                        model.return_value.invoke.return_value = AIMessage(content="JD 139 — Python developer\n\nBuild Python APIs.")
                        result = await asyncio.to_thread(graph.invoke, {"messages": [HumanMessage(content="Show JD 139")]}, config)
                    find.assert_awaited_once_with(owner, job_number=139, title=None, limit=5, offset=0)
                self.assertIn("Build Python APIs.", result["messages"][-1].content)
                self.assertFalse(result.get("jd_start_requested"))
                self.assertFalse(result.get("jd_conversation_id"))
                self.assertFalse(result.get("job_description_id"))

                saved = {"job_id": str(uuid4()), "status": "available", "screening_started": False,
                    "candidates": [{"name": "Ada", "rank": 1, "score": "90", "score_status": "current"}]}
                with patch.object(server.delivery, "resolve_job", new=AsyncMock(return_value=UUID(saved["job_id"]))), \
                     patch.object(server, "read_screening_results", new=AsyncMock(return_value=saved)) as read:
                    with patch("app.workflows.assistant.nodes.make_assistant_model") as model:
                        model.return_value.bind_tools.return_value.invoke.return_value = AIMessage(content="", tool_calls=[{
                            "id": "results", "name": "get_screening_results", "args": {"job_number": 139}}])
                        model.return_value.invoke.return_value = AIMessage(content="JD 139: Ada is ranked 1 with a saved score of 90.")
                        result = await asyncio.to_thread(graph.invoke, {"messages": [HumanMessage(content="Show screening results for JD 139")]}, config)
                    read.assert_awaited_once_with(owner, conversation_id, UUID(saved["job_id"]),
                        session_id=session_id, limit=5, offset=0, view="ranked")
                self.assertIn("Ada", result["messages"][-1].content)
                self.assertFalse(result.get("jd_start_requested"))
                self.assertFalse(result.get("job_description_id"))

                # Exercise the real graph → client → HTTP MCP → tool → graph path.
                jd_call = AIMessage(content="", tool_calls=[{"id": "jd", "name": "start_jd_creation", "args": {}}])
                with patch("app.workflows.assistant.nodes.make_assistant_model") as model:
                    model.return_value.bind_tools.return_value.invoke.return_value = jd_call
                    result = await asyncio.to_thread(graph.invoke, {"messages": [HumanMessage(content="Create a JD")]}, config)
                self.assertTrue(result["jd_start_requested"])
                self.assertEqual(result["messages"][-1].name, "start_jd_creation")

                # Preview and approval preserve the exact existing service contract.
                job_id, batch_id = uuid4(), uuid4()
                review = {"job_id": str(job_id), "batch_id": str(batch_id), "preview_key": "test-preview"}
                graph.update_state(config, {"jd_start_requested": False}, as_node="assistant_tools")
                with patch.object(server.delivery, "resolve_job", new_callable=AsyncMock, return_value=job_id), \
                     patch.object(server.delivery, "prepare_invitations", new_callable=AsyncMock, return_value=review):
                    preview = AIMessage(content="", tool_calls=[{"id": "preview", "name": "preview_interview_invitations", "args": {"job_number": 180}}])
                    with patch("app.workflows.assistant.nodes.make_assistant_model") as model:
                        model.return_value.bind_tools.return_value.invoke.return_value = preview
                        model.return_value.invoke.return_value = AIMessage(content="Here is the preview. Would you like me to send it?")
                        result = await asyncio.to_thread(graph.invoke, {"messages": [HumanMessage(content="Preview invitations")]}, config)
                self.assertEqual(result["invitation_review"]["batch_id"], str(batch_id))

                with patch.object(server.delivery, "confirm_invitations", new_callable=AsyncMock, return_value={"queued": 1}) as confirm:
                    confirmation = AIMessage(content="", tool_calls=[{"id": "confirm", "name": "confirm_interview_invitations", "args": {"approval_message": "Yes, send them"}}])
                    with patch("app.workflows.assistant.nodes.make_assistant_model") as model:
                        model.return_value.bind_tools.return_value.invoke.return_value = confirmation
                        model.return_value.invoke.return_value = AIMessage(content="One invitation queued.")
                        result = await asyncio.to_thread(graph.invoke, {"messages": [HumanMessage(content="Yes, send them")]}, config)
                    confirm.assert_awaited_once_with(job_id, owner, batch_id, "test-preview")
                self.assertIsNone(result["invitation_review"])

                with patch.object(server, "post_saved_job_to_discord", return_value={"status": "posted", "message_url": "https://example.com/mock"}) as post:
                    message, result = await client.execute_tool({"id": "post", "name": "post_job_to_discord", "args": {"job_number": 180}}, {}, config)
                    self.assertEqual(result["status"], "posted")
                    post.assert_called_once_with(owner, job_number=180, job_description_id=None)
                with patch.object(server.delivery, "resolve_job", new_callable=AsyncMock, return_value=job_id), \
                     patch.object(server.delivery, "invitation_status", new_callable=AsyncMock, return_value=[{"status": "queued"}]) as status:
                    message, result = await client.execute_tool({"id": "status", "name": "get_interview_invitation_status", "args": {"job_number": 180}}, {}, config)
                    self.assertEqual(result["invitations"][0]["status"], "queued")
                    status.assert_awaited_once_with(job_id, owner)

                # The server independently rejects approval without a shown preview.
                graph.update_state(config, {"invitation_review": None}, as_node="assistant_tools")
                confirmation_tools = await client.load_tools(config, ["confirm_interview_invitations"])
                confirm_tool = next(tool for tool in confirmation_tools if tool.name == "confirm_interview_invitations")
                with patch.object(server.delivery, "confirm_invitations", new_callable=AsyncMock) as confirm:
                    reply = await confirm_tool.ainvoke({"type": "tool_call", "id": "unreviewed", "name": confirm_tool.name, "args": {"approval_message": "Yes, send them"}})
                    self.assertEqual(reply.artifact["structured_content"]["status"], "failed")
                    confirm.assert_not_awaited()

                # Discovery-only credentials cannot perform actions; session/user checks stay active.
                async with httpx.AsyncClient() as http:
                    self.assertEqual((await http.post(url)).status_code, 401)
                    browser_session.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    headers = {"Authorization": "Bearer " + client.create_context_token(config, [])}
                    self.assertEqual((await http.post(url, headers=headers)).status_code, 401)
                    browser_session.expires_at = datetime.now(timezone.utc) + timedelta(days=1)
                    user.is_active = False
                    self.assertEqual((await http.post(url, headers=headers)).status_code, 403)
                    user.is_active = True
                    owned.side_effect = HTTPException(404, "Assistant conversation not found")
                    self.assertEqual((await http.post(url, headers=headers)).status_code, 404)
            finally:
                client.configure_mcp_loop(None)
                http_server.should_exit = True
                await asyncio.wait_for(serving, timeout=10)
                listener.close()


if __name__ == "__main__":
    unittest.main()
