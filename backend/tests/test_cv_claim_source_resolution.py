"""Exact pinned CV selection and grouping repair; no live model or database."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import AsyncMock, patch

from app.integrations.cv_model import ModelExecution
from app.services.interviews import post_interview_analysis as service, analysis


def inputs():
    text = "Header\nবাংলা 😀\r\nBuilt Python APIs.\nFooter"
    return {"rubric_version": "cv-claim-review-2", "model_options": asdict(ModelExecution("gemini", "test")),
        "criteria": [{"id": "criterion-python", "text": "Python"}], "references": [],
        "prepared": {"synthetic": True, "aligned_content_sha256": "transcript", "turns": [
            {"id": "answer", "speaker_role": "candidate", "text": "I built Python APIs."}]},
        "f5": {"artifact_sha256": "f5", "results": []}, "parent_run_id": "parent", "source_key": "source",
        "cv": {"documents": [{"parse_id": "parse", "file_version_id": "file", "text": text,
            "sources": [{"id": "page-one", "start": 7, "end": len(text)-7}]}]}}


def selected(quote="Built Python APIs."):
    return {"claims": [{"claim": "Built Python APIs", "criterion_ids": ["criterion-python"],
        "cv_evidence": {"source_ref": "cv-source-1", "quote": quote}, "status": "supported_in_interview",
        "explanation": "Candidate described the work.", "interview_evidence": [{"turn_id": "answer", "quote": "I built Python APIs."}],
        "same_context": True, "scope_comparison": "Same work.", "follow_up": "Ask about API design.", "uncertainty": None}],
        "selection_note": "One role-relevant claim selected."}


class SourceResolutionTests(unittest.TestCase):
    def test_backend_derives_unicode_and_page_relative_offsets_without_mutation(self):
        data, raw = inputs(), selected("বাংলা 😀\r\nBuilt Python APIs.")
        before = deepcopy((data, raw))
        result = service.materialize("cv_claim_review", raw, data)
        evidence = result["claims"][0]["cv_evidence"]
        self.assertEqual(evidence["start"], 7)
        self.assertEqual(evidence["source_id"], "page-one")
        self.assertEqual(data["cv"]["documents"][0]["text"][evidence["start"]:evidence["end"]], evidence["quote"])
        self.assertEqual((data, raw), before)

    def test_schema_only_requests_page_reference_and_quote_and_constrains_ids(self):
        schema = service.generation_schema("cv_claim_review", inputs())
        evidence = schema["$defs"]["CVSelection"]
        self.assertEqual(set(evidence["properties"]), {"source_ref", "quote"})
        self.assertEqual(evidence["properties"]["source_ref"]["enum"], ["cv-source-1"])
        self.assertEqual(schema["$defs"]["SelectedClaim"]["properties"]["criterion_ids"]["items"]["enum"], ["criterion-python"])

    def test_unknown_source_changed_quote_outside_page_and_unknown_criterion_rejected(self):
        cases = []
        raw = selected(); raw["claims"][0]["cv_evidence"]["source_ref"] = "invented"; cases.append((raw, "SOURCE_UNKNOWN"))
        cases.extend([(selected("Built Python APIs!"), "QUOTE_NOT_FOUND"),
                      (selected("Header"), "QUOTE_NOT_FOUND"), (selected("বাংলা 😀\nBuilt Python APIs."), "QUOTE_NOT_FOUND")])
        raw = selected(); raw["claims"][0]["criterion_ids"] = ["python"]; cases.append((raw, "CRITERION_INVALID"))
        for raw, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code):
                service.materialize("cv_claim_review", raw, inputs())

    def test_duplicate_occurrences_require_longer_unique_quote(self):
        data = inputs(); doc = data["cv"]["documents"][0]
        doc["text"] = "Python API. Another Python project."
        doc["sources"] = [{"id": "page-one", "start": 0, "end": len(doc["text"])}]
        with self.assertRaisesRegex(ValueError, "QUOTE_AMBIGUOUS"):
            service.materialize("cv_claim_review", selected("Python"), data)
        result = service.materialize("cv_claim_review", selected("Another Python project."), data)
        self.assertEqual(result["claims"][0]["cv_evidence"]["start"], 12)

    def test_correct_page_is_required_even_if_quote_exists_on_another_page(self):
        data = inputs(); doc = data["cv"]["documents"][0]
        doc["sources"] = [{"id": "header", "start": 0, "end": 6}, *doc["sources"]]
        with self.assertRaisesRegex(ValueError, "QUOTE_NOT_FOUND"):
            service.materialize("cv_claim_review", selected(), data)
        raw = selected(); raw["claims"][0]["cv_evidence"]["source_ref"] = "cv-source-2"
        self.assertEqual(service.materialize("cv_claim_review", raw, data)["claims"][0]["cv_evidence"]["source_id"], "page-one")

    def test_invalid_pinned_page_bounds_are_rejected_before_generation(self):
        data = inputs(); data["cv"]["documents"][0]["sources"][0]["end"] = 10000
        with self.assertRaisesRegex(ValueError, "PAGE_INVALID"):
            service.generation_schema("cv_claim_review", data)

    def test_duplicate_claim_and_candidate_attribution_checks_remain_strict(self):
        raw = selected(); raw["claims"].append(deepcopy(raw["claims"][0]))
        with self.assertRaisesRegex(ValueError, "DUPLICATE_CLAIM"):
            service.materialize("cv_claim_review", raw, inputs())
        raw = selected(); raw["claims"][0]["interview_evidence"] = []
        with self.assertRaisesRegex(ValueError, "CANDIDATE_EVIDENCE_REQUIRED"):
            service.materialize("cv_claim_review", raw, inputs())

    def test_legacy_contract_is_not_silently_reinterpreted(self):
        data = inputs(); data["rubric_version"] = "cv-claim-review-1"
        raw = selected(); quote = raw["claims"][0]["cv_evidence"]["quote"]
        start = data["cv"]["documents"][0]["text"].index(quote)
        raw["claims"][0]["cv_evidence"] = {"parse_id": "parse", "file_version_id": "file", "source_id": "page-one", "quote": quote, "start": start, "end": start+len(quote)}
        self.assertEqual(service.materialize("cv_claim_review", raw, data)["claims"][0]["cv_evidence"]["start"], start)
        raw["claims"][0]["cv_evidence"]["start"] += 1
        with self.assertRaisesRegex(ValueError, "QUOTE_NOT_FOUND"):
            service.materialize("cv_claim_review", raw, data)


class RepairTests(unittest.IsolatedAsyncioTestCase):
    async def execute(self, responses, stages=None):
        self.stages = deepcopy(stages or {})
        async def read(_claim, name): return deepcopy(self.stages.get(name))
        async def write(_claim, name, value): self.stages[name] = deepcopy(value)
        with patch.object(service.store, "read_stage", side_effect=read), patch.object(service.store, "write_stage", side_effect=write), \
             patch.object(service.store, "finish", AsyncMock()) as finish, patch.object(service, "generate_json", AsyncMock(side_effect=responses)) as generate:
            await service.execute({"kind": "cv_claim_review", "inputs": inputs()}, {"model": "test"})
            return finish.call_args.args[1], generate.call_args_list

    async def test_repair_receives_specific_index_and_previous_response_without_persisting_rejected_text(self):
        bad = selected("PRIVATE INVALID QUOTE")
        result, calls = await self.execute([json.dumps(bad), json.dumps(selected())])
        self.assertEqual(result["status"], "completed_synthetic")
        payload = json.loads(calls[1].args[1])
        self.assertEqual(payload["validation_error"]["field_path"], "claims.0.cv_evidence.quote")
        self.assertEqual(payload["previous_response"], bad)
        self.assertNotIn("PRIVATE INVALID QUOTE", json.dumps(self.stages))
        self.assertNotIn("cv_documents", payload)
        self.assertEqual(payload["cv_sources"][0]["source_ref"], "cv-source-1")

    async def test_exhausted_repair_budget_is_not_reset(self):
        result, calls = await self.execute([json.dumps(selected("wrong"))]*2)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(len(calls), 2)
        result, calls = await self.execute([], self.stages)
        self.assertEqual(result["status"], "needs_review")
        self.assertEqual(calls, [])

    async def test_mixed_greeting_root_failure_repair_identifies_excluded_prompt(self):
        state = {"turns": [{"id": "q", "speaker_role": "recruiter", "text": "Hello, describe your role and education.", "session_start_ms": 0, "session_end_ms": 1000},
            {"id": "a", "speaker_role": "candidate", "text": "I build Python APIs.", "session_start_ms": 1000, "session_end_ms": 2000}],
            "model_options": asdict(ModelExecution("gemini", "test"))}
        bad = {"groups": [{"id": "g1", "topic": "Role", "answer_state": "answered"}], "assignments": ["exclude:greeting", "g1:answer"]}
        good = {**bad, "assignments": ["g1:question", "g1:answer"]}
        with patch.object(analysis, "stage_read", AsyncMock(return_value=None)), patch.object(analysis, "stage_write", AsyncMock()), \
             patch.object(analysis, "generate_json", AsyncMock(side_effect=[json.dumps(bad), json.dumps(good)])) as generate:
            result = await analysis.segment_questions(state)
        self.assertEqual(result["groups"][0]["question_turn_ids"], ["q"])
        repair = generate.call_args_list[1].args[0]
        self.assertIn("excluded_recruiter_turns", repair)
        self.assertIn("t001", repair)
        self.assertIn("greetings that contain a complete prompt", repair)
