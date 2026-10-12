import unittest
from types import SimpleNamespace
from unittest.mock import patch
from langchain_core.messages import HumanMessage
from app.workflows.assistant.nodes import assistant_turn


class AssistantErrorTests(unittest.TestCase):
    def test_rate_limit_is_specific_and_routing_uses_low_reasoning_budget(self):
        RateLimitError = type("RateLimitError", (Exception,), {})
        settings = SimpleNamespace(groq_model="openai/gpt-oss-20b",
            groq_api_key=SimpleNamespace(get_secret_value=lambda: "test-key"))
        with patch("app.workflows.assistant.nodes.get_settings", return_value=settings), \
             patch("app.workflows.assistant.nodes.ChatGroq") as model:
            model.return_value.bind_tools.return_value.invoke.side_effect = RateLimitError()
            result = assistant_turn({"messages": [HumanMessage(content="Create a senior HR job description")]}, {})
        self.assertEqual(result["error_code"], "ASSISTANT_MODEL_RATE_LIMITED")
        self.assertEqual(model.call_args.kwargs["reasoning_effort"], "low")


if __name__ == "__main__":
    unittest.main()
