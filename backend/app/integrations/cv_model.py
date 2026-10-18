"""Bounded CV model transport, independent of JD Groq and local embeddings."""
import asyncio
from dataclasses import dataclass
from typing import Literal
import httpx
from app.core.config import get_settings
from app.integrations.gemini_rate_limit import acquire_gemini_slot, pause_gemini_requests, retry_delay

API = "https://generativelanguage.googleapis.com/v1beta/models/"
ADAPTER_VERSION = "cv-model-transport-1"


@dataclass(frozen=True)
class ModelExecution:
    """Explicit feature options; omitted options preserve existing CV behavior."""

    provider: Literal["ollama", "gemini"]
    model: str
    context_tokens: int = 32768
    output_tokens: int = 4096
    timeout_seconds: int = 300

    def __post_init__(self):
        if self.provider not in {"ollama", "gemini"} or not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("MODEL_EXECUTION_INVALID")
        if any(type(value) is not int for value in (self.context_tokens, self.output_tokens, self.timeout_seconds)):
            raise ValueError("MODEL_EXECUTION_INVALID")
        if not 4096 <= self.context_tokens <= 1048576 or not 256 <= self.output_tokens < self.context_tokens:
            raise ValueError("MODEL_EXECUTION_INVALID")
        if not 10 <= self.timeout_seconds <= 900:
            raise ValueError("MODEL_EXECUTION_INVALID")


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


def ollama_structural_schema(schema):
    """Avoid large bounded-array/pattern grammars on local llama.cpp backends.

    Keep types, required fields and enums; application validators retain every
    numeric, length, pattern, ownership and evidence constraint after generation.
    Existing CV calls keep their original schema for backward compatibility.
    """
    return gemini_schema(schema)


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


async def gemini_request(client, method, url, *, timeout_seconds=None, **kwargs):
    settings = get_settings()
    for attempt in range(settings.gemini_max_retries + 1):
        await acquire_gemini_slot()
        # Queue and cooldown waiting do not consume the network timeout.
        async with asyncio.timeout(timeout_seconds or settings.cv_model_timeout_seconds):
            response = await client.request(method, url, headers=headers(), **kwargs)
        if response.status_code == 429:
            await pause_gemini_requests(retry_delay(response, attempt))
            if attempt < settings.gemini_max_retries:
                continue
        check_response(response)
        return response


async def model_version(*, execution: ModelExecution | None = None):
    settings = get_settings()
    selected_provider = execution.provider if execution else provider()
    selected_model = execution.model if execution else model_name()
    adapter_version = ADAPTER_VERSION + ":explicit-core-schema-1" if execution else ADAPTER_VERSION
    async with httpx.AsyncClient(timeout=15) as client:
        if selected_provider == "gemini":
            response = await gemini_request(client, "GET", API + selected_model)
            check_response(response)
            data = response.json()
            if "generateContent" not in data.get("supportedGenerationMethods", []):
                raise ValueError("MODEL_NOT_AVAILABLE")
            return {"model": selected_model, "model_digest": "google:" + data.get("version", selected_model),
                    "provider": "gemini", "model_adapter": adapter_version}
        response = await client.get(settings.ollama_base_url.rstrip("/") + "/api/tags")
        response.raise_for_status()
    model = next((m for m in response.json().get("models", []) if m["name"] == selected_model), None)
    if not model or not model.get("digest"):
        raise ValueError("CV_MODEL_NOT_INSTALLED")
    return {"model": selected_model, "model_digest": model["digest"], "provider": "ollama", "model_adapter": adapter_version}


async def generate_json(system, payload, schema, output_error, *, execution: ModelExecution | None = None):
    settings = get_settings()
    selected_provider = execution.provider if execution else provider()
    selected_model = execution.model if execution else model_name()
    timeout = execution.timeout_seconds if execution else settings.cv_model_timeout_seconds
    output_tokens = execution.output_tokens if execution else settings.cv_model_output_tokens
    context = execution.context_tokens if execution else settings.cv_model_context_tokens
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10)) as client:
        # Gemini manages its timeout per attempt, after acquiring a rate-limit slot.
        async with asyncio.timeout(None if selected_provider == "gemini" else timeout):
            if selected_provider == "gemini":
                response = await gemini_request(client, "POST", API + selected_model + ":generateContent", timeout_seconds=timeout, json={
                    "systemInstruction": {"parts": [{"text": system.replace("/no_think", "")}]},
                    "contents": [{"role": "user", "parts": [{"text": payload}]}],
                    "generationConfig": {"responseFormat": {"text": {"mimeType": "APPLICATION_JSON", "schema": gemini_schema(schema)}},
                                         "maxOutputTokens": output_tokens}})
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
                    "model": selected_model, "stream": False, "think": False,
                    "format": ollama_structural_schema(schema) if execution else schema, "keep_alive": "1m",
                    "options": {"temperature": 0, "num_ctx": context,
                                "num_predict": output_tokens, "num_thread": 4},
                    "messages": [{"role": "system", "content": system}, {"role": "user", "content": payload}]})
                response.raise_for_status()
                data = response.json()
                if data.get("done_reason") == "length":
                    raise ValueError(output_error)
                raw = data.get("message", {}).get("content", "")
    if len(raw) > 100000:
        raise ValueError(output_error)
    return raw
