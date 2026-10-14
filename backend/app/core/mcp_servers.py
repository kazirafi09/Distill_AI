"""Shared MCP connections; credentials are supplied by each client."""
from app.core.config import get_settings

# Only these servers expose tools to the central assistant.
# CV screening and the email worker retain their own connections.
ASSISTANT_MCP_SERVERS = ("recruitment",)


def get_mcp_servers():
    settings = get_settings()
    return {
        "recruitment": {
            "transport": "http",
            "url": settings.recruitment_mcp_url,
        },
        "email": {
            "transport": "http",
            "url": settings.email_mcp_url,
        },
        "cv": {
            "transport": "http",
            "url": settings.cv_mcp_url,
        },
    }
