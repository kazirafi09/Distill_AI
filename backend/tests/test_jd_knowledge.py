import unittest
from unittest.mock import patch
from langchain_core.messages import AIMessage, HumanMessage
from app.services.jd.screening_profiles import ScreeningProfile, normalize_profile
from app.workflows.jd.nodes import RequirementGatheringResult, gather_requirements, generate_jd, invoke_jd_tool_model
from test_jd_profiles import profile


class KnowledgeTests(unittest.TestCase):
    def test_rate_limit_has_specific_recoverable_error(self):
        error = RuntimeError("provider rate limit")
        error.body = {"error": {"code": "rate_limit_exceeded"}}
        with patch("app.workflows.jd.nodes.get_settings") as settings, \
             patch("app.workflows.jd.nodes._make_model") as model:
            settings.return_value.groq_api_key.get_secret_value.return_value = "test-key"
            model.return_value.bind_tools.return_value.invoke.side_effect = error
            for node in ("gather_requirements", "generate_jd"):
                response, result = invoke_jd_tool_model({}, node, [HumanMessage(content="Senior HR")])
                self.assertIsNone(response)
                self.assertEqual(result["error_code"], "JD_MODEL_RATE_LIMITED")

    def test_empty_reference_planning_response_does_not_fail_drafting(self):
        with patch("app.workflows.jd.nodes.get_settings") as settings, \
             patch("app.workflows.jd.nodes._make_model") as model:
            settings.return_value.groq_api_key.get_secret_value.return_value = "test-key"
            model.return_value.bind_tools.return_value.invoke.return_value = AIMessage(content="")
            response, result = invoke_jd_tool_model({}, "gather_requirements", [HumanMessage(content="Senior HR")])
            self.assertIsNotNone(response)
            self.assertIsNone(result["error_code"])
            response, result = invoke_jd_tool_model({}, "generate_jd", [HumanMessage(content="Senior HR")])
            self.assertIsNone(response)
            self.assertEqual(result["error_code"], "JD_EMPTY_RESPONSE")

    def test_provider_tool_schema_failure_retries_in_json_mode(self):
        error = RuntimeError("provider rejected function arguments")
        error.body = {"error": {"code": "tool_use_failed"}}
        result = RequirementGatheringResult(requirements="Developer", requirements_ready=True,
            follow_up_question="", screening_profile=ScreeningProfile.model_validate({
                **profile(), "minimum_years_experience": None, "fresher_eligibility": None,
                "google_form_url": None}), allow_incomplete_draft=False)
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.side_effect = [error, {"parsed": result}]
            gathered = gather_requirements({"messages": [HumanMessage(content="Must have Knowledge of Python")]})
        self.assertEqual(gathered["status"], "drafting")
        methods = [call.kwargs["method"] for call in model.return_value.with_structured_output.call_args_list]
        self.assertEqual(methods, ["function_calling", "json_mode"])

    def test_authorized_suggestions_are_distinct_from_supplied_criteria(self):
        p = profile()
        quote = "Create a Senior HR job description from your knowledge"
        p.update(minimum_years_experience=None, fresher_eligibility=None, google_form_url=None,
                 weights_confirmed=False, weights_confirmation_quote=None)
        p["criteria"] = [{"id": "", "dimension": "technical_skills", "text": "HR information systems",
            "priority": "Must have", "source_quote": quote, "priority_quote": None, "source": "suggested"}]
        with self.assertRaises(ValueError):
            normalize_profile(ScreeningProfile.model_validate(p), None, [HumanMessage(content=quote)])
        normalized = normalize_profile(ScreeningProfile.model_validate(p), None,
            [HumanMessage(content=quote)], allow_suggestions=True)
        self.assertEqual(normalized["criteria"][0]["source"], "suggested")
        self.assertEqual(normalized["criteria"][0]["priority"], "Must have")
        self.assertFalse(normalized["weights_confirmed"])
        result = RequirementGatheringResult(requirements=quote, requirements_ready=True,
            follow_up_question="", screening_profile=ScreeningProfile.model_validate(p),
            allow_incomplete_draft=False, use_role_knowledge=True, knowledge_authorization_quote=quote)
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
            gathered = gather_requirements({"messages": [HumanMessage(content=quote)]})
        self.assertTrue(gathered["use_role_knowledge"])
        self.assertEqual(gathered["status"], "drafting")
        self.assertEqual(gathered["screening_profile"]["criteria"][0]["text"], "HR information systems")

    def test_revision_generation_receives_previous_draft(self):
        with patch("app.workflows.jd.nodes._draft_result", return_value={"status": "failed"}) as draft:
            generate_jd({"requirements": "Senior HR", "screening_profile": profile(), "profile_version": 1,
                "feedback": "Make it more detailed", "previous_draft_jd": "# Senior HR\n\nExisting responsibilities"})
        self.assertIn("Existing responsibilities", draft.call_args.args[2])

    def test_model_inferred_experience_is_kept_as_suggestion_not_recruiter_fact(self):
        p = profile()
        quote = "Suggest typical HR experience from your knowledge"
        p.update(criteria=[], minimum_years_experience="5 years", fresher_eligibility="Not eligible",
            google_form_url="https://forms.gle/invented", weights_confirmed=False, weights_confirmation_quote=None)
        normalized = normalize_profile(ScreeningProfile.model_validate(p), None, [HumanMessage(content=quote)],
            allow_suggestions=True, suggestion_authorization_quote=quote)
        self.assertIsNone(normalized["minimum_years_experience"])
        self.assertIsNone(normalized["fresher_eligibility"])
        self.assertIsNone(normalized["google_form_url"])
        self.assertEqual([c["source"] for c in normalized["criteria"]], ["suggested", "suggested"])
        self.assertIn("5 years", normalized["criteria"][0]["text"])


if __name__ == "__main__":
    unittest.main()
