"""Bounded CV model transport, independent of JD Groq and local embeddings."""
import asyncio
import httpx
from app.core.config import get_settings
from app.integrations.gemini_rate_limit import acquire_gemini_slot, pause_gemini_requests, retry_delay

API = "https://generativelanguage.googleapis.com/v1beta/models/"
ADAPTER_VERSION = "cv-model-transport-1"


def gemini_schema(schema):
    """Inline local Pydantic refs and send Gemini's supported core schema.

    Defaults and string/collection limits remain enforced by Python validation;
    omitting them here avoids provider schema compilation rejection.
    """
    definitions = schema.get("$defs", {})
    allowed = {"type", "properties", "required", "items", "anyOf", "enum", "additionalProperties", "description"}
    def convert(node):
        if isinstance(node, list):
            return [convert(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return convert(definitions[node["$ref"].split("/")[-1]])
        return {key: ({name: convert(value) for name, value in item.items()} if key == "properties" else convert(item))
                for key, item in node.items() if key in allowed}
    return convert(schema)


def provider():
    settings = get_settings()
    return ("gemini" if settings.gemini_api_key and settings.gemini_api_key.get_secret_value().strip() else "ollama") if settings.cv_model_provider == "auto" else settings.cv_model_provider


def model_name():
    settings = get_settings()
    return settings.gemini_cv_model if provider() == "gemini" else settings.cv_parser_model


def context_tokens():
    settings = get_settings()
    return settings.cv_gemini_context_tokens if provider() == "gemini" else settings.cv_model_context_tokens


def headers():
    key = get_settings().gemini_api_key
    if not key or not key.get_secret_value().strip():
        raise ValueError("MODEL_API_KEY_MISSING")
    # Never put the secret in the URL or diagnostic messages.
    return {"x-goog-api-key": key.get_secret_value()}


def check_response(response):
    if response.status_code >= 400:
        code = {400: "MODEL_REQUEST_INVALID", 401: "MODEL_AUTH_FAILED", 403: "MODEL_AUTH_FAILED",
                404: "MODEL_NOT_AVAILABLE", 429: "MODEL_RATE_LIMITED"}.get(response.status_code, "MODEL_UNAVAILABLE")
        raise ValueError(code)


async def gemini_request(client, method, url, **kwargs):
    settings = get_settings()
    for attempt in range(settings.gemini_max_retries + 1):
        await acquire_gemini_slot()
        # Queue and cooldown waiting do not consume the network timeout.
        async with asyncio.timeout(settings.cv_model_timeout_seconds):
            response = await client.request(method, url, headers=headers(), **kwargs)
        if response.status_code == 429:
            await pause_gemini_requests(retry_delay(response, attempt))
            if attempt < settings.gemini_max_retries:
                continue
        check_response(response)
        return response


async def model_version():
    settings = get_settings()
    async with httpx.AsyncClient(timeout=15) as client:
        if provider() == "gemini":
            response = await gemini_request(client, "GET", API + model_name())
            check_response(response)
            data = response.json()
            if "generateContent" not in data.get("supportedGenerationMethods", []):
                raise ValueError("MODEL_NOT_AVAILABLE")
            return {"model": model_name(), "model_digest": "google:" + data.get("version", model_name()),
                    "provider": "gemini", "model_adapter": ADAPTER_VERSION}
        response = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
        response.raise_for_status()
    model = next((m for m in response.json().get("models", []) if m["name"] == model_name()), None)
    if not model or not model.get("digest"):
        raise ValueError("CV_MODEL_NOT_INSTALLED")
    return {"model": model_name(), "model_digest": model["digest"], "provider": "ollama", "model_adapter": ADAPTER_VERSION}


async def generate_json(system, payload, schema, output_error):
    settings = get_settings()
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.cv_model_timeout_seconds, connect=10)) as client:
        # Gemini manages its timeout per attempt, after acquiring a rate-limit slot.
        async with asyncio.timeout(None if provider() == "gemini" else settings.cv_model_timeout_seconds):
            if provider() == "gemini":
                response = await gemini_request(client, "POST", API + model_name() + ":generateContent", json={
                    "systemInstruction": {"parts": [{"text": system.replace("/no_think", "")}]},
                    "contents": [{"role": "user", "parts": [{"text": payload}]}],
                    "generationConfig": {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": gemini_schema(schema)}},
                                         "maxOutputTokens": settings.cv_model_output_tokens}})
                check_response(response)
                data = response.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    raise ValueError("MODEL_RESPONSE_BLOCKED")
                candidate = candidates[0]
                if candidate.get("finishReason") == "MAX_TOKENS":
                    raise ValueError(output_error)
                if candidate.get("finishReason") != "STOP":
                    raise ValueError("MODEL_RESPONSE_BLOCKED")
                raw = "".join(part.get("text", "") for part in candidate.get("content", {}).get("parts", []) if not part.get("thought"))
            else:
                response = await client.post(settings.ollama_base_url.rstrip("/") + "/api/chat", json={
                    "model": model_name(), "stream": False, "think": False, "format": schema, "keep_alive": "1m",
                    "options": {"temperature": 0, "num_ctx": settings.cv_model_context_tokens,
                                "num_predict": settings.cv_model_output_tokens, "num_thread": 4},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": payload}]})
                response.raise_for_status()
                data = response.json()
                if data.get("done_reason") == "length":
                    raise ValueError(output_error)
                raw = data.get("message", {}).get("content", "")
    if len(raw) > 100000:
        raise ValueError(output_error)
    return raw
