"""A Recruitment MCP proxy may read results for one job, never start CV work."""
import asyncio
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.mcp_servers import get_mcp_servers

RESULTS_AUDIENCE = "cv-mcp-results"


class ResultsClaims(BaseModel):
    recruiter_id: UUID = Field(alias="sub")
    conversation_id: UUID
    job_id: UUID
    tool: Literal["get_screening_results"]
    sid: UUID | None = None


def create_results_token(recruiter_id, conversation_id, job_id, session_id=None):
    now = datetime.now(timezone.utc)
    claims = {"sub": str(recruiter_id), "conversation_id": str(conversation_id),
        "job_id": str(job_id), "tool": "get_screening_results", "aud": RESULTS_AUDIENCE,
        "iat": now, "exp": now + timedelta(seconds=90)}
    if session_id is not None:
        claims["sid"] = str(session_id)
    # Validate before signing, including the conversation required by delegation.
    ResultsClaims.model_validate(claims)
    return jwt.encode(claims, get_settings().jwt_secret_key.get_secret_value(), algorithm="HS256")


async def read_screening_results(recruiter_id, conversation_id, job_id, *, session_id=None,
                                 limit=5, offset=0, view="ranked"):
    token = create_results_token(recruiter_id, conversation_id, job_id, session_id)
    async with asyncio.timeout(25):
        async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=20) as http:
            async with streamable_http_client(get_mcp_servers()["cv"]["url"], http_client=http) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    reply = await session.call_tool("get_screening_results",
                        {"job_id": str(job_id), "limit": limit, "offset": offset, "view": view})
    content = reply.structuredContent or {}
    result = content.get("result")
    if reply.isError or not isinstance(result, dict) or result.get("job_id") != str(job_id):
        raise ValueError("CV results did not match the authorized job")
    return result
