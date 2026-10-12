"""Client connection to the custom email MCP server."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID

import httpx
import jwt
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.core.config import get_settings
from app.integrations.gmail import EmailMessageRequest, EmailSendResult


class EmailJob(EmailMessageRequest):
    job_id: UUID


def message_hash(job: EmailJob) -> str:
    content = json.dumps(
        job.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def signing_secret() -> str:
    secret = get_settings().email_mcp_secret
    if secret is None or len(secret.get_secret_value()) < 32:
        raise ValueError("Email MCP secret requires at least 32 characters.")
    return secret.get_secret_value()


def create_job_token(job: EmailJob) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(job.job_id),
            "aud": "email-mcp",
            "iat": now,
            "exp": now + timedelta(minutes=2),
            "message_hash": message_hash(job),
        },
        signing_secret(),
        algorithm="HS256",
    )


async def send_email_job(job: EmailJob) -> EmailSendResult:
    try:
        token = create_job_token(job)
    except ValueError:
        return EmailSendResult(
            status="failed",
            error_code="EMAIL_MCP_NOT_CONFIGURED",
        )

    send_started = False
    try:
        async with asyncio.timeout(60):
            async with httpx.AsyncClient(
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            ) as client:
                async with streamable_http_client(
                    get_settings().email_mcp_url,
                    http_client=client,
                ) as (read, write, _):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        send_started = True
                        result = await session.call_tool(
                            "send_email",
                            {"job": job.model_dump(mode="json")},
                        )

        if result.isError or not result.structuredContent:
            return EmailSendResult(
                status="uncertain",
                error_code="EMAIL_MCP_RESULT_UNAVAILABLE",
            )

        return EmailSendResult.model_validate(result.structuredContent)
    except Exception:
        # Once the tool call starts, a lost response may hide an accepted send.
        return EmailSendResult(
            status="uncertain" if send_started else "failed",
            error_code="EMAIL_MCP_CONNECTION_ERROR",
        )
