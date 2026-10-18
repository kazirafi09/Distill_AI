"""Authenticated MCP server for individual email jobs."""
from contextlib import asynccontextmanager
from contextvars import ContextVar

import jwt
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount

from app.integrations.email_mcp import EmailJob, message_hash, signing_secret
from app.integrations.gmail import EmailSendResult, send_email as deliver

authorized_job: ContextVar[dict] = ContextVar("email_mcp_job")

mcp = FastMCP(
    "Distill AI Email",
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=["localhost:*", "127.0.0.1:*", "email-mcp:*"],
        allowed_origins=["http://localhost:*", "http://127.0.0.1:*"],
    ),
)


@mcp.tool(structured_output=True)
async def send_email(job: EmailJob) -> EmailSendResult:
    """Send exactly the email authorized by the queue worker."""
    claims = authorized_job.get()
    if (
        claims["sub"] != str(job.job_id)
        or claims["message_hash"] != message_hash(job)
    ):
        raise ValueError("Email does not match the authorized job.")
    return await deliver(job)


class EmailAuth:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        try:
            secret = signing_secret()
        except ValueError:
            return await JSONResponse(
                {"detail": "Email MCP is not configured."}, status_code=503,
            )(scope, receive, send)

        authorization = dict(scope.get("headers", [])).get(
            b"authorization", b""
        ).decode("latin-1")
        scheme, _, token = authorization.partition(" ")

        try:
            if scheme.lower() != "bearer" or not token:
                raise ValueError("Missing credential.")
            claims = jwt.decode(
                token, secret, algorithms=["HS256"], audience="email-mcp",
                options={"require": ["sub", "aud", "iat", "exp", "message_hash"]},
            )
        except (jwt.InvalidTokenError, ValueError):
            return await JSONResponse(
                {"detail": "Invalid email job credential."}, status_code=401,
            )(scope, receive, send)

        context_token = authorized_job.set(claims)
        try:
            await self.app(scope, receive, send)
        finally:
            authorized_job.reset(context_token)


@asynccontextmanager
async def lifespan(app):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[Mount("/mcp", app=EmailAuth(mcp.streamable_http_app()))],
    lifespan=lifespan,
)
