import unittest
from types import SimpleNamespace
from unittest.mock import patch
from langchain_core.messages import HumanMessage
from app.workflows.assistant.nodes import assistant_turn


class AssistantErrorTests(unittest.TestCase):
    def test_scope_rate_limit_is_specific_and_does_not_route_an_action(self):
        RateLimitError = type("RateLimitError", (Exception,), {})
        settings = SimpleNamespace(gemini_api_key=SimpleNamespace(get_secret_value=lambda: "test-key"))
        with patch("app.workflows.assistant.nodes.get_settings", return_value=settings), \
             patch("app.workflows.assistant.nodes.discover_assistant_tools",
                   return_value=[SimpleNamespace(name="start_jd_creation")]), \
             patch("app.workflows.assistant.nodes.make_assistant_model") as model:
            model.return_value.with_structured_output.return_value.invoke.side_effect = RateLimitError()
            result = assistant_turn({"messages": [HumanMessage(content="Create a senior HR job description")]}, {})
        self.assertEqual(result["error_code"], "ASSISTANT_MODEL_RATE_LIMITED")
        model.return_value.bind_tools.assert_not_called()


if __name__ == "__main__":
    unittest.main()
