"""Shared MCP server connections; request credentials stay in each client."""
from app.core.config import get_settings


def get_mcp_servers():
    settings = get_settings()
    return {
        "email": {
            "transport": "http",
            "url": settings.email_mcp_url,
        },
        "cv": {
            "transport": "http",
            "url": settings.cv_mcp_url,
        },
    }
