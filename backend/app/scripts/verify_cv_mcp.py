"""Real HTTP MCP client: token is read from env, never a command-line argument."""
import argparse
import asyncio
import os
from datetime import timedelta

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file_version_id")
    parser.add_argument("--url", default="http://127.0.0.1:8000/mcp/")
    parser.add_argument("--parse", action="store_true")
    args = parser.parse_args()
    token = os.environ["DISTILL_ACCESS_TOKEN"]
    async with httpx.AsyncClient() as client:
        denied = await client.post(args.url, json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert denied.status_code == 401, denied.status_code
    async with streamablehttp_client(args.url, headers={"Authorization": f"Bearer {token}"},
                                    timeout=timedelta(seconds=900), sse_read_timeout=timedelta(seconds=900)) as (read, write, _):
        async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=900)) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"parse_cv", "get_cv_profile"}.issubset(names), names
            result = await session.call_tool("parse_cv" if args.parse else "get_cv_profile", {"file_version_id": args.file_version_id})
            assert not result.isError, result
            assert result.structuredContent is not None, "Server must return a structured parsing result"
            assert result.structuredContent["file_version_id"] == args.file_version_id
            import json
            print(json.dumps(result.structuredContent, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
