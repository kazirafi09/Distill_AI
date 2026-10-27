"""Explicit F5 execution must not change CV defaults or silently choose an API."""

import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app.integrations.cv_model import ModelExecution, generate_json, model_version, ollama_structural_schema


class ModelExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_ollama_does_not_consult_cv_auto_provider(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"message": {"content": '{"ok":true}'}, "done_reason": "stop"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        options = ModelExecution("ollama", "qwen3:4b", 16384, 512, 60)
        with patch("app.integrations.cv_model.httpx.AsyncClient", return_value=client), \
             patch("app.integrations.cv_model.provider", side_effect=AssertionError("CV auto used")), \
             patch("app.integrations.cv_model.model_name", side_effect=AssertionError("CV model used")):
            result = await generate_json("test", "data", {"type": "object"}, "TOO_LARGE", execution=options)
        body = json.loads(requests[0].content)
        self.assertEqual(result, '{"ok":true}')
        self.assertTrue(str(requests[0].url).endswith("/api/chat"))
        self.assertEqual(body["model"], "qwen3:4b")
        self.assertEqual(body["options"]["num_ctx"], 16384)
        self.assertEqual(body["options"]["num_predict"], 512)

    async def test_existing_cv_default_provider_is_preserved(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"ok":true}'}]}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("app.integrations.cv_model.httpx.AsyncClient", return_value=client), \
             patch("app.integrations.cv_model.provider", return_value="gemini"), \
             patch("app.integrations.cv_model.model_name", return_value="cv-test-model"), \
             patch("app.integrations.cv_model.headers", return_value={"x-goog-api-key": "test-secret"}), \
             patch("app.integrations.cv_model.acquire_gemini_slot", new_callable=AsyncMock):
            result = await generate_json("test", "data", {"type": "object"}, "TOO_LARGE")
        self.assertEqual(result, '{"ok":true}')
        self.assertIn("cv-test-model:generateContent", str(requests[0].url))
        self.assertNotIn("test-secret", str(requests[0].url))

    async def test_explicit_model_version_uses_its_own_name(self):
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,
            json={"models": [{"name": "f5-test", "digest": "fixed-digest"}]})))
        with patch("app.integrations.cv_model.httpx.AsyncClient", return_value=client), \
             patch("app.integrations.cv_model.provider", side_effect=AssertionError("CV auto used")):
            version = await model_version(execution=ModelExecution("ollama", "f5-test"))
        self.assertEqual(version["model_digest"], "fixed-digest")

    async def test_explicit_gemini_uses_f5_model_and_shared_rate_limiter(self):
        requests = []

        def respond(request):
            requests.append(request)
            return httpx.Response(200, json={"candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"ok":true}'}]}}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        with patch("app.integrations.cv_model.httpx.AsyncClient", return_value=client), \
             patch("app.integrations.cv_model.provider", side_effect=AssertionError("CV provider used")), \
             patch("app.integrations.cv_model.headers", return_value={"x-goog-api-key": "test-key"}), \
             patch("app.integrations.cv_model.acquire_gemini_slot", new_callable=AsyncMock) as acquire:
            raw = await generate_json("test", "data", {"type": "object"}, "TOO_LARGE",
                execution=ModelExecution("gemini", "f5-configured-model", 65536, 1024, 60))
        self.assertEqual(raw, '{"ok":true}')
        self.assertIn("f5-configured-model:generateContent", str(requests[0].url))
        self.assertEqual(json.loads(requests[0].content)["generationConfig"]["maxOutputTokens"], 1024)
        acquire.assert_awaited_once()

    def test_invalid_options_do_not_enable_auto_fallback(self):
        for provider, model, context, output, timeout in [
            ("auto", "qwen3:4b", 32768, 4096, 300), ("ollama", "", 32768, 4096, 300),
            ("ollama", "qwen3:4b", 100, 4096, 300), ("ollama", "qwen3:4b", 8192, 8192, 300),
        ]:
            with self.subTest(provider=provider, model=model, context=context), self.assertRaises(ValueError):
                ModelExecution(provider, model, context, output, timeout)

    def test_local_schema_keeps_structure_without_large_grammar_constraints(self):
        from app.services.interviews.analysis_schemas import Grouping, DimensionEvaluation
        grouping = ollama_structural_schema(Grouping.model_json_schema())
        evaluation = ollama_structural_schema(DimensionEvaluation.model_json_schema())
        self.assertEqual(set(grouping["required"]), {"groups", "excluded_turns"})
        self.assertNotIn("$ref", str(grouping))
        self.assertNotIn("maxItems", str(grouping))
        self.assertNotIn("pattern", str(grouping))
        self.assertEqual(evaluation["properties"]["dimension"]["enum"], ["relevance", "accuracy", "completeness", "articulation"])
        self.assertIn({"type": "null"}, evaluation["properties"]["score"]["anyOf"])


if __name__ == "__main__":
    unittest.main()
