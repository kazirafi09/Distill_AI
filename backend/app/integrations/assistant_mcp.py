"""Discover and execute assistant tools through configured MCP servers."""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import jwt
from langchain_core.messages import ToolMessage
from langchain_mcp_adapters.client import MultiServerMCPClient

from app.core.config import get_settings
from app.core.mcp_servers import ASSISTANT_MCP_SERVERS, get_mcp_servers

api_loop = None


def configure_mcp_loop(loop):
    """The existing graph runs in threads; MCP uses FastAPI's event loop."""
    global api_loop
    api_loop = loop


def run_mcp(operation):
    if api_loop is None or api_loop.is_closed():
        operation.close()
        raise RuntimeError("Assistant MCP connection is unavailable")
    future = asyncio.run_coroutine_threadsafe(operation, api_loop)
    try:
        return future.result(timeout=45)
    except Exception:
        future.cancel()
        raise


def create_context_token(config, allowed_tools):
    context = config.get("configurable", {})
    thread_id = context.get("thread_id", "")
    if not thread_id.startswith("assistant:"):
        raise ValueError("Assistant conversation context is required")
    now = datetime.now(timezone.utc)
    claims = {
        "sub": str(UUID(context["user_id"])),
        "aud": "recruitment-mcp",
        "iat": now,
        "exp": now + timedelta(seconds=90),
        "conversation_id": str(UUID(thread_id.removeprefix("assistant:"))),
        "allowed_tools": allowed_tools,
    }
    if context.get("auth_session_id"):
        claims["sid"] = str(UUID(context["auth_session_id"]))
    return jwt.encode(
        claims, get_settings().jwt_secret_key.get_secret_value(), algorithm="HS256",
    )


def permitted_tools(tools, state):
    """Keep existing workflow guards without hardcoding a tool inventory."""
    return [
        tool for tool in tools
        if (tool.name != "start_jd_creation" or not state.get("jd_conversation_id"))
        and (tool.name != "confirm_interview_invitations" or state.get("invitation_review"))
    ]


async def load_tools(config, allowed_tools):
    token = create_context_token(config, allowed_tools)
    servers = get_mcp_servers()
    connections = {
        name: {**servers[name], "headers": {"Authorization": f"Bearer {token}"}}
        for name in ASSISTANT_MCP_SERVERS
    }
    # No retries for tool actions. Discovery credentials alone cannot execute tools.
    async with asyncio.timeout(30):
        tools = await MultiServerMCPClient(connections).get_tools()
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("Configured MCP servers returned duplicate tool names")
    return tools


def discover_assistant_tools(state, config):
    return permitted_tools(run_mcp(load_tools(config, [])), state)


async def execute_tool(call, state, config):
    # Refresh credentials for this one action; do not cache recruiter tokens.
    tools = permitted_tools(await load_tools(config, [call["name"]]), state)
    tool = next((tool for tool in tools if tool.name == call["name"]), None)
    if tool is None:
        raise ValueError("The requested tool is not available in this conversation")
    async with asyncio.timeout(30):
        response = await tool.ainvoke({**call, "type": "tool_call"})
    if not isinstance(response, ToolMessage):
        raise ValueError("MCP tool returned an invalid message")
    artifact = response.artifact or {}
    result = artifact.get("structured_content")
    if response.status == "error" or not isinstance(result, dict):
        raise ValueError("MCP tool did not return a successful structured result")
    # Preserve the JSON format expected by invitation history and model prompts.
    return ToolMessage(
        name=call["name"], tool_call_id=call["id"],
        content=json.dumps(result, ensure_ascii=False),
    ), result


def call_assistant_tool(call, state, config):
    return run_mcp(execute_tool(call, state, config))
