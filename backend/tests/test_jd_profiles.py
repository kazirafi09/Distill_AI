"""Deterministic JD stage checks; no LLM, candidate processing or indexing."""
import asyncio
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from app.services.jd.screening_profiles import (
    DIMENSIONS, ScreeningProfile, acceptance_errors, normalize_profile,
    profile_errors, public_profile_section, supported_form_url,
)
from app.services.jd.conversations import act_on_jd_conversation, save_approved_jd
from app.services.assistant.conversations import send_assistant_jd_action
from app.workflows.jd.graph import build_jd_graph
from app.workflows.jd.nodes import RequirementGatheringResult, gather_requirements


def profile():
    return ScreeningProfile.model_validate({
        "criteria": [{"id": "criterion-test", "dimension": "technical_skills", "text": "Knowledge of Python",
                      "priority": "Must have", "source_quote": "Knowledge of Python", "priority_quote": "Must have"}],
        "addressed_dimensions": list(DIMENSIONS),
        "minimum_years_experience": "minimum 1.5 years", "fresher_eligibility": "freshers are not eligible",
        "dimension_weights": {"technical_skills": 100, "work_experience": 0,
                              "qualifications": 0, "additional_criteria": 0},
        "weights_confirmed": True,
        "weights_confirmation_quote": "technical skills 100%",
        "google_form_url": "https://forms.gle/abc123",
    }).model_dump(mode="json")


def state():
    p = profile()
    return {"requirements": "Recruiter-supplied role", "screening_profile": p,
            "profile_version": 1, "draft_profile_version": 1,
            "draft_jd": "# Python Developer\n\n" + public_profile_section(p),
            "status": "awaiting_review", "revision_count": 0}


class ProfileTests(unittest.TestCase):
    def test_form_formats_only(self):
        for url in ("https://forms.gle/a-b_C", "https://docs.google.com/forms/d/id/viewform",
                    "https://docs.google.com/forms/d/e/id/viewform?usp=sf_link"):
            self.assertTrue(supported_form_url(url), url)
        for url in (None, "http://forms.gle/id", "https://forms.gle.evil/id", "https://forms.gle/id/extra",
                    "https://docs.google.com/forms/d/id/edit", "https://user@forms.gle/id", "https://forms.gle/id#x"):
            self.assertFalse(supported_form_url(url), url)

    def test_valid_profile_and_consistent_draft(self):
        self.assertEqual(acceptance_errors(state()), [])
        public = public_profile_section(profile())
        self.assertNotIn("100", public)
        self.assertNotIn("criterion-test", public)
        self.assertIn("minimum 1.5 years", public)
        self.assertIn("freshers are not eligible", public)

    def test_invalid_acceptance_cases(self):
        mutations = [
            lambda s: s.update(draft_profile_version=0),
            lambda s: s.update(draft_jd="# Outdated draft"),
            lambda s: s.update(draft_jd=""),
            lambda s: s.update(draft_jd=s["draft_jd"] + "\nDimension weights: 100%"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                s = state()
                mutate(s)
                self.assertTrue(acceptance_errors(s))

    def test_incomplete_screening_allows_acceptance(self):
        from app.services.cv.scoring import validate_scoring_profile

        s = state()
        p = s["screening_profile"]
        p.update(google_form_url=None, weights_confirmed=False, weights_confirmation_quote=None,
                 addressed_dimensions=["technical_skills"])
        p["criteria"][0].update(priority=None, priority_quote=None)
        p["dimension_weights"]["technical_skills"] = 90
        s["draft_jd"] = "# Python Developer\n\n" + public_profile_section(p)
        self.assertEqual(acceptance_errors(s), [])
        self.assertTrue(profile_errors(p))
        with self.assertRaises(ValueError):
            validate_scoring_profile(p, 1)

    def test_jd_without_screening_profile_can_be_saved(self):
        self.assertEqual(acceptance_errors({"draft_jd": "# Developer", "screening_profile": None}), [])

    def test_decimal_weights(self):
        p = profile()
        p["criteria"].extend([
            {**p["criteria"][0], "id": "c2", "dimension": "qualifications"},
            {**p["criteria"][0], "id": "c3", "dimension": "work_experience"},
        ])
        p["dimension_weights"] = {"technical_skills": 33.3, "qualifications": 33.3,
                                  "work_experience": 33.4, "additional_criteria": 0}
        self.assertEqual(profile_errors(p), [])
        p["dimension_weights"]["technical_skills"] = float("nan")
        self.assertTrue(profile_errors(p))

    def test_ids_and_verbatim_evidence(self):
        p = profile()
        messages = [HumanMessage(content="Must have Knowledge of Python; minimum 1.5 years; freshers are not eligible; qualifications: none; additional selection criteria: none; technical skills 100%; https://forms.gle/abc123")]
        normalized = normalize_profile(ScreeningProfile.model_validate(p), p, messages)
        self.assertEqual(normalized, p)
        p["criteria"][0]["text"] = "Python familiarity"
        self.assertEqual(normalize_profile(ScreeningProfile.model_validate(p), profile(), messages)["criteria"][0]["id"], "criterion-test")
        p["minimum_years_experience"] = "minimum 2 years"
        with self.assertRaises(ValueError):
            normalize_profile(ScreeningProfile.model_validate(p), profile(), messages)

    def test_ambiguous_priority_and_unconfirmed_suggestion(self):
        p = profile()
        p.update(minimum_years_experience=None, fresher_eligibility=None, google_form_url=None)
        p["criteria"][0]["priority_quote"] = "Knowledge of Python"
        messages = [HumanMessage(content="Knowledge of Python"), AIMessage(content="Use technical skills 100%?")]
        result = normalize_profile(ScreeningProfile.model_validate(p), None, messages)
        self.assertIsNone(result["criteria"][0]["priority"])
        self.assertFalse(result["weights_confirmed"])

    def test_changed_weights_cannot_reuse_old_confirmation(self):
        p = profile()
        messages = [HumanMessage(content="Knowledge of Python; Must have; minimum 1.5 years; freshers are not eligible; technical skills 100%; https://forms.gle/abc123")]
        p["dimension_weights"] = {"technical_skills": 50, "work_experience": 0,
                                  "qualifications": 0, "additional_criteria": 0}
        result = normalize_profile(ScreeningProfile.model_validate(p), profile(), messages)
        self.assertFalse(result["weights_confirmed"])

    def test_weights_suggestion_requires_contextual_confirmation(self):
        p = profile()
        p.update(minimum_years_experience=None, fresher_eligibility=None, google_form_url=None, weights_confirmation_quote="yes")
        previous = copy.deepcopy(p)
        previous["weights_confirmed"] = False
        initial = HumanMessage(content="Must have Knowledge of Python")
        confirmed = normalize_profile(ScreeningProfile.model_validate(p), previous,
            [initial, AIMessage(content="Use technical skills weight 100%?"), HumanMessage(content="yes")])
        self.assertTrue(confirmed["weights_confirmed"])
        unrelated = normalize_profile(ScreeningProfile.model_validate(p), previous,
            [initial, AIMessage(content="Keep the location?"), HumanMessage(content="yes")])
        self.assertFalse(unrelated["weights_confirmed"])

    def test_zero_weight_for_unused_dimension(self):
        p = profile()
        p["dimension_weights"]["work_experience"] = 0
        self.assertEqual(profile_errors(p), [])

    def test_confirmation_does_not_depend_on_model_flag_or_repeating_numbers(self):
        p = profile()
        p.update(minimum_years_experience=None, fresher_eligibility=None,
                 google_form_url=None, weights_confirmed=False,
                 weights_confirmation_quote=None)
        initial = HumanMessage(content="Must have Knowledge of Python")
        question = AIMessage(content="Are the screening dimension weights you provided confirmed?")
        reply = HumanMessage(content="Yes the weights are confirmed")
        confirmed = normalize_profile(ScreeningProfile.model_validate(p), p,
                                      [initial, question, reply])
        self.assertTrue(confirmed["weights_confirmed"])
        self.assertEqual(confirmed["weights_confirmation_quote"], reply.content)
        carried = normalize_profile(ScreeningProfile.model_validate(p), confirmed,
            [initial, question, reply, AIMessage(content="Any additional criteria?"),
             HumanMessage(content="No additional criteria.")])
        self.assertTrue(carried["weights_confirmed"])
        unrelated = normalize_profile(ScreeningProfile.model_validate(p), p,
            [initial, AIMessage(content="Keep the location?"), reply])
        self.assertFalse(unrelated["weights_confirmed"])
        changed = copy.deepcopy(p)
        changed["dimension_weights"]["technical_skills"] = 90
        self.assertFalse(normalize_profile(ScreeningProfile.model_validate(changed), p,
            [initial, question, reply])["weights_confirmed"])

    def test_recruiter_weights_with_comma_in_experience_label(self):
        p = profile()
        p.update(minimum_years_experience=None, fresher_eligibility=None,
                 google_form_url=None, weights_confirmed=False,
                 weights_confirmation_quote=None)
        p["criteria"].extend([
            {**p["criteria"][0], "id": "experience", "dimension": "work_experience",
             "text": "Relevant Python projects", "source_quote": "Relevant Python projects"},
            {**p["criteria"][0], "id": "qualification", "dimension": "qualifications",
             "text": "CSE degree", "source_quote": "CSE degree"},
        ])
        p["dimension_weights"] = {"technical_skills": 50, "work_experience": 30,
                                  "qualifications": 20, "additional_criteria": 0}
        result = normalize_profile(ScreeningProfile.model_validate(p), None, [HumanMessage(content=(
            "Must have Knowledge of Python; Relevant Python projects; CSE degree.\n"
            "Technical skills: 50%\nRelevant experience, including projects: 30%\nQualifications: 20%"
        ))])
        self.assertTrue(result["weights_confirmed"])

    def test_explicit_no_additional_criteria_is_addressed(self):
        p = profile()
        p["addressed_dimensions"] = ["technical_skills"]
        messages = [HumanMessage(content=(
            "Must have Knowledge of Python; minimum 1.5 years; freshers are not "
            "eligible; technical skills 100%; https://forms.gle/abc123. "
            "There are no other additional selection criteria."
        ))]
        normalized = normalize_profile(ScreeningProfile.model_validate(p), p, messages)
        self.assertIn("additional_criteria", normalized["addressed_dimensions"])
        self.assertIn("work_experience", normalized["addressed_dimensions"])

    def test_explicit_priority_lists_ground_exact_criteria(self):
        incoming = profile()
        incoming["criteria"] = [
            {**incoming["criteria"][0], "id": "", "text": "Write tests and fix bugs",
             "source_quote": "Write tests and fix bugs"},
            {**incoming["criteria"][0], "id": "", "dimension": "qualifications",
             "text": "Bachelor degree in Computer Science", "source_quote": "Bachelor degree in Computer Science"},
            {**incoming["criteria"][0], "id": "", "priority": "Preferred",
             "text": "Familiarity with FastAPI", "source_quote": "Familiarity with FastAPI",
             "priority_quote": "Preferred"},
        ]
        incoming["dimension_weights"] = {
            "technical_skills": 50, "work_experience": 0,
            "qualifications": 50, "additional_criteria": 0,
        }
        incoming["minimum_years_experience"] = None
        incoming["fresher_eligibility"] = None
        incoming["google_form_url"] = None
        incoming["weights_confirmation_quote"] = "Technical skills: 50%\nQualifications: 50%"
        message = HumanMessage(content=(
            "Responsibilities:\n- Write tests and fix bugs\n\nMust have:\n"
            "- A bachelor's degree in CSE\n\nPreferred:\n- Familiarity with FastAPI\n\n"
            "Technical skills: 50%\nQualifications: 50%"
        ))
        result = normalize_profile(ScreeningProfile.model_validate(incoming), None, [message])
        self.assertEqual(
            [(item["text"], item["priority"]) for item in result["criteria"]],
            [("A bachelor's degree in CSE", "Must have"),
             ("Familiarity with FastAPI", "Preferred")],
        )

    def test_explicit_draft_request_proceeds_when_only_form_is_missing(self):
        p = profile()
        p["google_form_url"] = None
        result = RequirementGatheringResult(
            requirements="x", requirements_ready=False, follow_up_question="",
            screening_profile=ScreeningProfile.model_validate(p),
            allow_incomplete_draft=False,
        )
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
            messages = [HumanMessage(content=(
                "Must have Knowledge of Python; minimum 1.5 years; freshers are "
                "not eligible; qualifications: none; additional selection criteria: "
                "none; technical skills 100%. Draft now without a form."
            ))]
            self.assertEqual(gather_requirements({"messages": messages})["status"], "drafting")

    def test_gathering_rejects_more_than_two_questions(self):
        result = RequirementGatheringResult(requirements="x", requirements_ready=False,
            follow_up_question="Skills? Experience? Qualifications?", screening_profile=ScreeningProfile.model_validate(profile()),
            allow_incomplete_draft=False)
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
            messages = [HumanMessage(content="Must have Knowledge of Python; minimum 1.5 years; freshers are not eligible; qualifications: none; additional selection criteria: none; technical skills 100%; https://forms.gle/abc123")]
            self.assertEqual(gather_requirements({"messages": messages})["status"], "failed")
            self.assertEqual(model.return_value.with_structured_output.call_args.kwargs["method"], "function_calling")

    def test_unconfirmed_weights_allow_drafting_with_screening_warnings(self):
        p = profile()
        p["google_form_url"] = None
        result = RequirementGatheringResult(requirements="x", requirements_ready=True,
            follow_up_question="", screening_profile=ScreeningProfile.model_validate(p),
            allow_incomplete_draft=False)
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
            messages = [HumanMessage(content="Must have Knowledge of Python; minimum 1.5 years; freshers are not eligible; qualifications: none; additional selection criteria: none")]
            gathered = gather_requirements({"messages": messages})
            self.assertEqual(gathered["status"], "drafting")
            self.assertEqual(gathered["follow_up_question"], "")
            self.assertFalse(gathered["screening_profile"]["weights_confirmed"])
            self.assertTrue(profile_errors(gathered["screening_profile"]))

    def test_incomplete_screening_does_not_override_draft_readiness(self):
        p = profile()
        p.update(minimum_years_experience=None, fresher_eligibility=None,
                 google_form_url=None, weights_confirmed=False,
                 weights_confirmation_quote=None)
        p["criteria"][0].update(priority=None, priority_quote=None)
        p["dimension_weights"]["technical_skills"] = 0
        result = RequirementGatheringResult(
            requirements="Python developer", requirements_ready=True,
            follow_up_question="Confirm weights?",
            screening_profile=ScreeningProfile.model_validate(p),
            allow_incomplete_draft=False,
        )
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
            gathered = gather_requirements({"messages": [HumanMessage(content="Python developer; Knowledge of Python")]})
        self.assertEqual(gathered["status"], "drafting")
        self.assertEqual(gathered["screening_profile"]["addressed_dimensions"], ["technical_skills"])
        self.assertIsNone(gathered["screening_profile"]["criteria"][0]["priority"])
        self.assertTrue(profile_errors(gathered["screening_profile"]))

    def test_explicit_early_draft_bypasses_screening_follow_up(self):
        for allowed, request in ((False, "Draft now."), (True, "Use what you have.")):
            with self.subTest(allow_incomplete_draft=allowed):
                p = profile()
                p.update(criteria=[], minimum_years_experience=None, fresher_eligibility=None,
                         google_form_url=None, weights_confirmed=False, weights_confirmation_quote=None)
                result = RequirementGatheringResult(
                    requirements="Python developer", requirements_ready=False,
                    follow_up_question="Qualifications?", screening_profile=ScreeningProfile.model_validate(p),
                    allow_incomplete_draft=allowed,
                )
                with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
                     patch("app.workflows.jd.nodes._make_model") as model:
                    model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
                    gathered = gather_requirements({"messages": [HumanMessage(content="Python developer"), HumanMessage(content=request)]})
                self.assertEqual(gathered["status"], "drafting")
                self.assertFalse(gathered["screening_profile"]["weights_confirmed"])

    def test_role_clarification_is_not_bypassed_by_old_or_negated_request(self):
        for latest in ("The role could be Python developer or recruiter.", "Do not draft until we choose the role."):
            with self.subTest(latest=latest):
                p = profile()
                p.update(criteria=[], minimum_years_experience=None, fresher_eligibility=None,
                         google_form_url=None, weights_confirmed=False, weights_confirmation_quote=None)
                result = RequirementGatheringResult(
                    requirements="Unclear role", requirements_ready=False,
                    follow_up_question="Which role should this JD describe?",
                    screening_profile=ScreeningProfile.model_validate(p), allow_incomplete_draft=False,
                )
                with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
                     patch("app.workflows.jd.nodes._make_model") as model:
                    model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
                    gathered = gather_requirements({"messages": [HumanMessage(content="Generate a JD."), HumanMessage(content=latest)]})
                self.assertEqual(gathered["status"], "gathering")
                self.assertIn("Which role", gathered["follow_up_question"])

    def test_missing_form_allows_drafting(self):
        p = profile()
        p["google_form_url"] = None
        result = RequirementGatheringResult(requirements="x", requirements_ready=True,
            follow_up_question="", screening_profile=ScreeningProfile.model_validate(p),
            allow_incomplete_draft=False)
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model:
            model.return_value.with_structured_output.return_value.invoke.return_value = {"parsed": result}
            messages = [HumanMessage(content="Must have Knowledge of Python; minimum 1.5 years; freshers are not eligible; qualifications: none; additional selection criteria: none; technical skills 100%")]
            self.assertEqual(gather_requirements({"messages": messages})["status"], "drafting")

    def test_unsupported_evidence_is_retried_without_weakening_validation(self):
        bad = profile()
        bad["criteria"][0]["text"] = "invented unsupported skill"
        bad["criteria"][0]["source_quote"] = "paraphrased unsupported skill"
        invalid = RequirementGatheringResult(
            requirements="x", requirements_ready=True, follow_up_question="",
            screening_profile=ScreeningProfile.model_validate(bad),
            allow_incomplete_draft=False,
        )
        valid = RequirementGatheringResult(
            requirements="x", requirements_ready=True, follow_up_question="",
            screening_profile=ScreeningProfile.model_validate(profile()),
            allow_incomplete_draft=False,
        )
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model, \
             self.assertLogs("distill_ai.workflows.jd", level="WARNING") as logs:
            model.return_value.with_structured_output.return_value.invoke.side_effect = [
                {"parsed": invalid}, {"parsed": valid},
            ]
            messages = [HumanMessage(content="Must have Knowledge of Python; minimum 1.5 years; freshers are not eligible; qualifications: none; additional selection criteria: none; technical skills 100%; https://forms.gle/abc123")]
            result = gather_requirements({"messages": messages})
        self.assertEqual(result["status"], "drafting")
        self.assertTrue(any("criteria.0.source_quote" in item for item in logs.output))


class GraphTests(unittest.TestCase):
    def test_role_only_draft_and_revision_reach_review_with_incomplete_profile(self):
        p = profile()
        p.update(criteria=[], minimum_years_experience=None, fresher_eligibility=None,
                 google_form_url=None, weights_confirmed=False, weights_confirmation_quote=None)
        results = [RequirementGatheringResult(
            requirements="Python developer", requirements_ready=ready,
            follow_up_question="" if ready else "Confirm weights?",
            screening_profile=ScreeningProfile.model_validate(p), allow_incomplete_draft=False,
        ) for ready in (True, False)]
        with patch("app.workflows.jd.nodes.invoke_jd_tool_model", return_value=(AIMessage(content="done"), {})), \
             patch("app.workflows.jd.nodes._make_model") as model, \
             patch("app.workflows.jd.nodes._draft_result", side_effect=[
                 {"draft_jd": "# Python Developer", "status": "awaiting_review"},
                 {"draft_jd": "# Junior Python Developer", "status": "awaiting_review"},
             ]):
            model.return_value.with_structured_output.return_value.invoke.side_effect = [{"parsed": item} for item in results]
            graph = build_jd_graph(InMemorySaver())
            config = {"configurable": {"thread_id": str(uuid4())}}
            graph.invoke({"messages": [HumanMessage(content="The role is Python developer.")]}, config)
            first = graph.get_state(config)
            self.assertEqual(first.values["status"], "awaiting_review")
            self.assertEqual(acceptance_errors(first.values), [])
            self.assertTrue(profile_errors(first.values["screening_profile"]))
            graph.invoke(Command(resume={"action": "revise", "feedback": "Change the title to Junior Python Developer."}), config)
            revised = graph.get_state(config)
            self.assertEqual(revised.values["status"], "awaiting_review")
            self.assertIn("# Junior Python Developer", revised.values["draft_jd"])
            self.assertEqual(revised.values["revision_count"], 1)
            self.assertEqual(revised.values["draft_profile_version"], revised.values["profile_version"])
            self.assertEqual(acceptance_errors(revised.values), [])
            graph.invoke(Command(resume={"action": "approve"}), config)
            self.assertEqual(graph.get_state(config).values["status"], "approved")

    def test_unlimited_revision_updates_profile_and_draft(self):
        def gather(s):
            p = copy.deepcopy(s.get("screening_profile") or profile())
            latest = s["messages"][-1].content
            if latest.startswith("Edit"):
                p["criteria"][0]["text"] = latest
            return {"requirements": latest, "screening_profile": p, "requirements_ready": True,
                    "profile_version": s.get("profile_version", 0) + 1, "status": "drafting"}
        with patch("app.workflows.jd.graph.gather_requirements", side_effect=gather), \
             patch("app.workflows.jd.nodes._draft_result", return_value={"draft_jd": "# Developer", "status": "awaiting_review"}):
            graph = build_jd_graph(InMemorySaver())
            config = {"configurable": {"thread_id": str(uuid4())}}
            graph.invoke({"messages": [HumanMessage(content="Create JD")]}, config)
            for i in range(7):
                graph.invoke(Command(resume={"action": "revise", "feedback": f"Edit {i}"}), config)
                s = graph.get_state(config).values
                self.assertEqual(s["revision_count"], i + 1)
                self.assertIn(f"Edit {i}", s["draft_jd"])
                self.assertEqual(s["screening_profile"]["criteria"][0]["id"], "criterion-test")
                self.assertEqual(s["draft_profile_version"], s["profile_version"])
            graph.invoke(Command(resume={"action": "approve"}), config)
            self.assertEqual(graph.get_state(config).values["status"], "approved")

    def test_graph_allows_approval_without_form(self):
        with patch("app.workflows.jd.graph.gather_requirements", return_value={**state(), "requirements_ready": True}), \
             patch("app.workflows.jd.nodes._draft_result", return_value={"draft_jd": "# Developer", "status": "awaiting_review"}):
            graph = build_jd_graph(InMemorySaver())
            config = {"configurable": {"thread_id": str(uuid4())}}
            graph.invoke({"messages": [HumanMessage(content="create")]}, config)
            snapshot = graph.get_state(config)
            s = copy.deepcopy(snapshot.values)
            s["screening_profile"]["google_form_url"] = None
            s["draft_jd"] = "# Developer\n\n" + public_profile_section(s["screening_profile"])
            graph.update_state(config, s, as_node="generate_jd")
            graph.invoke(None, config)
            graph.invoke(Command(resume={"action": "approve"}), config)
            self.assertEqual(graph.get_state(config).values["status"], "approved")


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_owned_conversation_required(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException) as error:
            await act_on_jd_conversation(db, None, uuid4(), uuid4(), checkpoint_id="x", interrupt_id="y", action="approve")
        self.assertEqual(error.exception.status_code, 404)

    async def test_direct_api_blocks_inconsistent_draft_before_graph_resume(self):
        s = state()
        s["draft_jd"] = "# Outdated draft"
        snapshot = SimpleNamespace(values=s, config={"configurable": {"checkpoint_id": "cp"}},
            tasks=[SimpleNamespace(interrupts=[SimpleNamespace(id="pause", value={"allowed_actions": ["approve", "revise", "cancel"]})])])
        graph = SimpleNamespace(get_state=lambda config: snapshot, invoke=AsyncMock())
        with patch("app.services.jd.conversations.get_owned_conversation", new=AsyncMock(return_value=SimpleNamespace(id=uuid4(), created_at=None))):
            with self.assertRaises(HTTPException) as error:
                await act_on_jd_conversation(None, graph, uuid4(), uuid4(), checkpoint_id="cp", interrupt_id="pause", action="approve")
            self.assertEqual(error.exception.status_code, 422)
            graph.invoke.assert_not_called()

    async def test_central_chatbot_uses_same_validator(self):
        assistant = SimpleNamespace(jd_conversation_id=uuid4())
        graph = SimpleNamespace(get_state=lambda config: SimpleNamespace(values={}, config={"configurable": {"checkpoint_id": "cp"}}))
        with patch("app.services.assistant.conversations.get_owned_assistant", new=AsyncMock(return_value=assistant)), \
             patch("app.services.assistant.conversations.act_on_jd_conversation", new=AsyncMock(side_effect=HTTPException(422, "Google Form required"))) as shared:
            with self.assertRaises(HTTPException):
                await send_assistant_jd_action(None, graph, None, uuid4(), uuid4(), checkpoint_id="cp", jd_checkpoint_id="jdcp", interrupt_id="i", action="approve")
            self.assertEqual(shared.call_args.kwargs["action"], "approve")

    async def test_retry_reuses_existing_legacy_document_without_fabrication(self):
        existing = SimpleNamespace(screening_profile=None)
        db = SimpleNamespace(scalar=AsyncMock(return_value=existing))
        result = await save_approved_jd(db, SimpleNamespace(id=uuid4()), {"status": "approved"})
        self.assertIs(result, existing)
        self.assertIsNone(result.screening_profile)

    async def test_retry_missing_save_requires_valid_document(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None))
        with self.assertRaises(HTTPException):
            await save_approved_jd(db, SimpleNamespace(id=uuid4()), {"status": "approved"})


if __name__ == "__main__":
    unittest.main()
