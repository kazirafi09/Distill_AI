"""Gemini chat with deployment-wide pacing, bounded retries and real streaming."""
from google import genai
from google.genai import types
from langchain_google_genai import ChatGoogleGenerativeAI

from app.core.config import get_settings
from app.integrations.gemini_rate_limit import (
    acquire_gemini_slot_sync, pause_gemini_requests_sync, retry_delay,
    acquire_gemini_slot, pause_gemini_requests,
)


def is_rate_limited(error):
    """SDK/LangChain wrappers retain the provider exception as their cause."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        body = getattr(error, "body", None)
        detail = body.get("error") if isinstance(body, dict) else None
        if (getattr(error, "code", None) == 429
                or type(error).__name__ in {"RateLimitError", "ResourceExhausted"}
                or str(error) == "MODEL_RATE_LIMITED"
                or (isinstance(detail, dict) and detail.get("code") == "rate_limit_exceeded")):
            return True
        error = error.__cause__ or error.__context__
    return False


def _cooldown(error, attempt):
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        response = getattr(error, "response", None)
        if response is not None:
            return retry_delay(response, attempt)
        details = getattr(error, "details", None)
        if isinstance(details, dict):
            # Reuse RetryInfo parsing for SDK errors without an HTTP response.
            class Response:
                headers = {}
                def json(self):
                    return details if "error" in details else {"error": details}
            return retry_delay(Response(), attempt)
        error = error.__cause__ or error.__context__
    class EmptyResponse:
        headers = {}
        def json(self):
            return {}
    return retry_delay(EmptyResponse(), attempt)


def _normalize(message):
    # Existing workflows use the provider-neutral truncation marker and text.
    if message.response_metadata.get("finish_reason") == "MAX_TOKENS":
        message.response_metadata["finish_reason"] = "length"
    if isinstance(message.content, list):
        message.content = "".join(
            part if isinstance(part, str) else part.get("text", "")
            for part in message.content
            if isinstance(part, str) or (isinstance(part, dict) and part.get("type") == "text")
        )
    return message


class GeminiChatModel(ChatGoogleGenerativeAI):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        for attempt in range(get_settings().gemini_max_retries + 1):
            acquire_gemini_slot_sync()
            try:
                result = super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
                for generation in result.generations:
                    _normalize(generation.message)
                return result
            except Exception as exc:
                if not is_rate_limited(exc):
                    raise
                pause_gemini_requests_sync(_cooldown(exc, attempt))
                if attempt >= get_settings().gemini_max_retries:
                    raise

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        for attempt in range(get_settings().gemini_max_retries + 1):
            acquire_gemini_slot_sync()
            emitted = False
            try:
                for chunk in super()._stream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    emitted = True
                    _normalize(chunk.message)
                    yield chunk
                return
            except Exception as exc:
                if not is_rate_limited(exc):
                    raise
                pause_gemini_requests_sync(_cooldown(exc, attempt))
                # Never replay a partially delivered stream or tool-call fragment.
                if emitted or attempt >= get_settings().gemini_max_retries:
                    raise

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        for attempt in range(get_settings().gemini_max_retries + 1):
            await acquire_gemini_slot()
            try:
                result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **kwargs)
                for generation in result.generations:
                    _normalize(generation.message)
                return result
            except Exception as exc:
                if not is_rate_limited(exc):
                    raise
                await pause_gemini_requests(_cooldown(exc, attempt))
                if attempt >= get_settings().gemini_max_retries:
                    raise

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        for attempt in range(get_settings().gemini_max_retries + 1):
            await acquire_gemini_slot()
            emitted = False
            try:
                async for chunk in super()._astream(messages, stop=stop, run_manager=run_manager, **kwargs):
                    emitted = True
                    _normalize(chunk.message)
                    yield chunk
                return
            except Exception as exc:
                if not is_rate_limited(exc):
                    raise
                await pause_gemini_requests(_cooldown(exc, attempt))
                if emitted or attempt >= get_settings().gemini_max_retries:
                    raise


def make_chat_model(max_tokens=4096, *, settings=None):
    settings = settings or get_settings()
    if not settings.gemini_api_key or not settings.gemini_api_key.get_secret_value().strip():
        raise ValueError("GEMINI_NOT_CONFIGURED")
    model = GeminiChatModel(model=settings.gemini_chat_model, api_key=settings.gemini_api_key,
        max_output_tokens=max_tokens, include_thoughts=False, max_retries=0, timeout=60,
        vertexai=False)
    # Disable hidden SDK retries: each actual attempt must acquire a shared slot.
    model.client.close()
    model.client = genai.Client(api_key=settings.gemini_api_key.get_secret_value(), vertexai=False,
        http_options=types.HttpOptions(timeout=60000, retry_options=types.HttpRetryOptions(attempts=1)))
    return model
