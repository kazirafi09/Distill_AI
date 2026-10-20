"""SSE transport around the existing conversation transaction."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

import anyio
from fastapi import HTTPException
from fastapi.encoders import jsonable_encoder
from starlette.responses import StreamingResponse

from app.workflows.streaming import stream_replies


logger = logging.getLogger(__name__)


class ConversationStreamResponse(StreamingResponse):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # Some ASGI servers report disconnects as a failed send rather than
            # cancelling iteration. Close explicitly before DB dependencies exit.
            with anyio.CancelScope(shield=True):
                await self.body_iterator.aclose()


def conversation_stream(operation: Callable[[], Awaitable[dict]]) -> StreamingResponse:
    async def events():
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()
        connected = True

        def enqueue(event):
            if connected:
                queue.put_nowait(event)

        def emit(event):
            # Model calls run in worker threads; asyncio queues are not thread-safe.
            loop.call_soon_threadsafe(enqueue, event)

        async def run():
            try:
                with stream_replies(emit):
                    result = await operation()
                enqueue({"type": "complete", "conversation": result})
            except HTTPException as exc:
                enqueue({"type": "error", "status": exc.status_code, "detail": exc.detail})
            except Exception:
                logger.exception("Assistant streaming request failed")
                enqueue({"type": "error", "status": 500, "detail":
                         "Unable to finish the reply. Reload the conversation before retrying."})

        task = asyncio.create_task(run())
        try:
            yield _event({"type": "started"})
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=10)
                except TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield _event(event)
                if event["type"] in {"complete", "error"}:
                    break
        finally:
            connected = False
            # Stopping the browser must not cancel an external tool halfway
            # through and leave it eligible for an accidental duplicate retry.
            # Keep the request's DB dependency alive until its transaction ends.
            with anyio.CancelScope(shield=True):
                await task

    return ConversationStreamResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-store", "X-Accel-Buffering": "no",
    })


def _event(event: dict) -> str:
    return "data: " + json.dumps(jsonable_encoder(event), ensure_ascii=False) + "\n\n"
