"""Opt-in text streaming for user-facing model calls only.

The context follows existing thread-pool and graph calls without putting an
HTTP connection or callback into durable graph state.
"""

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar

from langchain_core.messages import message_chunk_to_message


_emit: ContextVar[Callable[[dict], None] | None] = ContextVar("chat_stream", default=None)


@contextmanager
def stream_replies(emit: Callable[[dict], None]):
    token = _emit.set(emit)
    try:
        yield
    finally:
        _emit.reset(token)


def invoke_reply(model, messages, *, prefix: str = ""):
    """Return the same complete message while optionally emitting text chunks.

    Never emit tool arguments, reasoning metadata, or structured extraction.
    A tool turn clears its provisional text; only the saved response is final.
    """
    emit = _emit.get()
    if emit is None:
        return model.invoke(messages)

    emit({"type": "reset"})
    if prefix:
        emit({"type": "delta", "text": prefix})
    complete = None
    tool_turn = False
    try:
        for chunk in model.stream(messages):
            complete = chunk if complete is None else complete + chunk
            if chunk.tool_call_chunks and not tool_turn:
                tool_turn = True
                emit({"type": "reset"})
            if not tool_turn and isinstance(chunk.content, str) and chunk.content:
                emit({"type": "delta", "text": chunk.content})
        if complete is None:
            raise ValueError("The model returned an empty stream")
        return message_chunk_to_message(complete)
    except Exception:
        emit({"type": "reset"})
        raise
