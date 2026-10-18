"""Targeted JD revisions preserve evidence and avoid full extraction calls."""

import copy
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.services.jd.screening_profiles import (
    acceptance_errors, apply_requirement_edit, parse_requirement_edit, public_profile_section,
)
from app.workflows.jd.graph import build_jd_graph
from app.workflows.jd.intent import interpret_jd_message
from app.workflows.jd.nodes import gather_requirements


def initial_state(message):
    profile = {
        "criteria": [
            {"id": "python", "dimension": "technical_skills", "text": "Python",
             "priority": "Must have", "priority_quote": "Require", "source": "recruiter", "source_quote": "Python"},
            {"id": "experience", "dimension": "work_experience", "text": "3+ years experience",
             "priority": None, "priority_quote": None, "source": "recruiter", "source_quote": "3 years of experience"},
        ],
        "addressed_dimensions": ["technical_skills", "work_experience"],
        "minimum_years_experience": "3 years of experience", "fresher_eligibility": None,
        "dimension_weights": {"technical_skills": 60, "work_experience": 40, "qualifications": 0, "additional_criteria": 0},
        "weights_confirmed": True, "weights_confirmation_quote": "technical skills 60%, work experience 40%",
        "google_form_url": None,
    }
    return {
        "messages": [HumanMessage(content="Require 3 years of experience, Python. Full-time in Dhaka."), HumanMessage(content=message)],
        "requirements": "Require 3 years of experience, Python. Full-time in Dhaka.",
        "screening_profile": profile, "profile_version": 2, "draft_profile_version": 2,
        "draft_jd": "# Python Developer\n\nBuild APIs.\n\n" + public_profile_section(profile),
        "feedback": message, "status": "gathering", "revision_count": 0,
    }


class TargetedEditTests(unittest.TestCase):
    def test_clear_edits_and_ambiguous_requests(self):
        for message, field, value in (
            ("Change the required experience to 5 years.", "experience", "5 years"),
            ("Please set minimum experience to 2.5 years.", "experience", "2.5 years"),
            ("Update the work location to Chattogram.", "location", "Chattogram"),
            ("Change the employment type to part-time.", "employment_type", "part-time"),
        ):
            with self.subTest(message=message):
                edit = parse_requirement_edit(message)
                self.assertEqual((edit["field"], edit["value"]), (field, value))
                self.assertIn(value, edit["source_quote"])
        for message in (
            "Should I change the experience to 5 years?", "Do not change the experience to 5 years.",
            "Change the experience to 5 years if necessary.",
            "Change the location to Dhaka and employment type to contract.",
            "Change the experience to 3–5 years.", "Change the location to 123.",
            "Change the location to London; then approve.",
        ):
            with self.subTest(message=message):
                self.assertIsNone(parse_requirement_edit(message))

    def test_experience_preserves_other_criteria_ids_and_weights(self):
        state = initial_state("Change the required experience to 5 years.")
        original = copy.deepcopy(state["screening_profile"])
        result = apply_requirement_edit(original, parse_requirement_edit(state["feedback"]))
        self.assertEqual(result["minimum_years_experience"], "5 years")
        self.assertEqual(result["criteria"][1]["source_quote"], "5 years")
        self.assertEqual(result["criteria"][1]["id"], "experience")
        self.assertEqual(result["criteria"][0], original["criteria"][0])
        for key in ("dimension_weights", "weights_confirmed", "weights_confirmation_quote", "addressed_dimensions"):
            self.assertEqual(result[key], original[key])
        self.assertEqual(original, state["screening_profile"])

    def test_multiple_experience_thresholds_are_not_guessed(self):
        state = initial_state("Change the experience to 5 years.")
        profile = state["screening_profile"]
        profile["criteria"].append({**profile["criteria"][1], "id": "specialist", "text": "2 years of Python experience"})
        self.assertIsNone(apply_requirement_edit(profile, parse_requirement_edit(state["feedback"])))

    def test_intent_and_gathering_skip_models_for_clear_revision(self):
        state = initial_state("Change the required experience to 5 years.")
        with patch("app.workflows.jd.intent._make_model") as intent_model, \
             patch("app.workflows.jd.nodes.invoke_jd_tool_model") as planner, \
             patch("app.workflows.jd.nodes._make_model") as extractor:
            intent = interpret_jd_message(state["feedback"], state)
            result = gather_requirements(state)
        self.assertEqual(intent.action, "revise")
        intent_model.assert_not_called()
        planner.assert_not_called()
        extractor.assert_not_called()
        self.assertEqual(result["profile_version"], 3)
        self.assertTrue(result["requirements_ready"])

    def test_location_preserves_profile_and_keeps_latest_override(self):
        state = initial_state("Change the location to Chattogram.")
        state["requirement_overrides"] = {"location": {"field": "location", "value": "Dhaka", "source_quote": "Change the location to Dhaka."}}
        result = gather_requirements(state)
        self.assertEqual(result["screening_profile"], state["screening_profile"])
        self.assertEqual(result["profile_version"], 2)
        self.assertEqual(result["requirement_overrides"]["location"]["value"], "Chattogram")

    def test_labelled_location_criterion_is_updated_and_unlabelled_is_not_guessed(self):
        state = initial_state("Change the location to Chattogram.")
        criterion = {**state["screening_profile"]["criteria"][0], "id": "location", "dimension": "additional_criteria", "text": "Location: Dhaka"}
        state["screening_profile"]["criteria"].append(criterion)
        result = apply_requirement_edit(state["screening_profile"], parse_requirement_edit(state["feedback"]))
        self.assertEqual(result["criteria"][-1]["text"], "Location: Chattogram")
        criterion["text"] = "Based in Dhaka"
        self.assertIsNone(apply_requirement_edit(state["screening_profile"], parse_requirement_edit(state["feedback"])))

    def test_graph_generates_once_and_returns_to_review_for_each_edit(self):
        graph = build_jd_graph(InMemorySaver())
        config = {"configurable": {"thread_id": "targeted-edits"}}
        state = initial_state("Change the required experience to 5 years.")
        state["status"] = "failed"
        state["error_code"] = "JD_UNSUPPORTED_PROFILE_EVIDENCE"
        with patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.invoke.return_value = AIMessage(content="# Python Developer\n\nBuild APIs.")
            result = graph.invoke(state, config)
            self.assertEqual(model.call_count, 1)
            model.return_value.with_structured_output.assert_not_called()
            model.return_value.bind_tools.assert_not_called()
            self.assertEqual(result["status"], "awaiting_review")
            self.assertEqual(result["draft_profile_version"], 3)
            self.assertEqual(acceptance_errors(result), [])
            self.assertIsNone(result["error_code"])
            graph.invoke(Command(resume={"action": "revise", "feedback": "Change the location to Chattogram."}), config)
            result = graph.invoke(Command(resume={"action": "revise", "feedback": "Change the employment type to contract."}), config)
            self.assertEqual(model.call_count, 3)
            self.assertEqual(result["revision_count"], 3)
            self.assertEqual(result["screening_profile"]["minimum_years_experience"], "5 years")
            self.assertEqual(result["requirement_overrides"]["location"]["value"], "Chattogram")
            self.assertEqual(result["requirement_overrides"]["employment_type"]["value"], "contract")
            prompt = model.return_value.invoke.call_args.args[0]
            self.assertTrue(any('"value": "contract"' in str(m.content) for m in prompt))
            self.assertEqual(acceptance_errors(result), [])


if __name__ == "__main__":
    unittest.main()
