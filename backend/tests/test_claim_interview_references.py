"""Claim-review references bind exact candidate speech without model-generated turn IDs."""
from copy import deepcopy
from dataclasses import asdict
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.integrations.cv_model import ModelExecution
from app.services.interviews import post_interview_analysis as service


def inputs():
    return {"rubric_version": "cv-claim-review-3", "criteria": [{"id": "python", "text": "Python"}],
        "cv": {"documents": [{"parse_id": "parse", "file_version_id": "file", "text": "Python APIs",
            "sources": [{"id": "page", "start": 0, "end": 11}]}]},
        "prepared": {"synthetic": True, "aligned_content_sha256": "saved", "turns": [
            {"id": "question-uuid", "speaker_role": "recruiter", "text": "Did you build Python APIs?", "session_start_ms": 0},
            {"id": "candidate-uuid", "speaker_role": "candidate", "text": "Yes. I built Python APIs.\r\nবাংলা 😀", "session_start_ms": 1000, "session_end_ms": 9000}]},
        "model_options": asdict(ModelExecution("gemini", "test")),
        "f5": {"artifact_sha256": "f5", "results": []}, "references": [], "parent_run_id": "parent", "source_key": "key"}


def selected(refs=None):
    return {"claims": [{"claim": "Built Python APIs", "criterion_ids": ["python"],
        "cv_evidence": {"source_ref": "cv-source-1", "quote": "Python APIs"},
        "interview_evidence": ["interview-source-1"] if refs is None else refs,
        "status": "supported_in_interview", "explanation": "The candidate describes that work.",
        "same_context": True, "scope_comparison": "Same role.", "follow_up": "Ask about API design.", "uncertainty": None}],
        "selection_note": "One role-relevant claim selected."}


class ReferenceTests(unittest.TestCase):
    def test_schema_restricts_references_to_candidate_excerpts(self):
        schema = service.generation_schema("cv_claim_review", inputs())
        field = schema["$defs"]["ReferencedClaim"]["properties"]["interview_evidence"]
        self.assertEqual(field["items"], {"type": "string", "enum": ["interview-source-1"]})
        self.assertNotIn("turn_id", json.dumps(field))
        self.assertNotIn("question-uuid", json.dumps(field))

    def test_backend_binds_exact_quote_speaker_turn_and_timing_without_mutation(self):
        data, raw = inputs(), selected()
        before = deepcopy((data, raw))
        result = service.materialize("cv_claim_review", raw, data)
        quote = result["claims"][0]["interview_evidence"][0]
        turn = data["prepared"]["turns"][1]
        self.assertEqual(quote, {"turn_id": turn["id"], "quote": turn["text"], "speaker_role": "candidate",
            "session_start_ms": 1000, "session_end_ms": 9000})
        self.assertEqual((data, raw), before)

    def test_long_answers_are_fully_preserved_as_bounded_exact_spans(self):
        data = inputs()
        text = ("বাংলা 😀: I built APIs, but I did not own the whole platform.\r\n" * 90) + "Final correction: I maintained only one endpoint."
        data["prepared"]["turns"][1]["text"] = text
        bank = service.candidate_excerpts(data)
        self.assertGreater(len(bank), 1)
        self.assertEqual("".join(item["quote"] for item in bank.values()), text)
        for item in bank.values():
            self.assertLessEqual(len(item["quote"]), 1200)
            self.assertEqual(item["quote"], text[item["start"]:item["end"]])
            self.assertEqual(item["continues_before"], item["start"] > 0)
            self.assertEqual(item["continues_after"], item["end"] < len(text))
        last = list(bank)[-1]
        result = service.materialize("cv_claim_review", selected([last]), data)
        self.assertIn("Final correction", result["claims"][0]["interview_evidence"][0]["quote"])

    def test_unknown_reference_raw_turn_ids_and_generated_quote_objects_are_rejected(self):
        for ref in ("invented", "question-uuid", "candidate-uuid"):
            with self.subTest(ref=ref), self.assertRaisesRegex(ValueError, "SOURCE_UNKNOWN") as caught:
                service.materialize("cv_claim_review", selected([ref]), inputs())
            diagnostic = service.validation_diagnostic(caught.exception)
            self.assertEqual(diagnostic["field_path"], "claims.0.interview_evidence.0")
            self.assertNotIn(ref, json.dumps(diagnostic))
        with self.assertRaises(ValidationError):
            service.materialize("cv_claim_review", selected([{"turn_id": "candidate-uuid", "quote": "I built APIs"}]), inputs())

    def test_repeated_phrases_do_not_require_guessing_the_speaker_or_turn(self):
        data = inputs()
        data["prepared"]["turns"][0]["text"] = "I built Python APIs."
        data["prepared"]["turns"].append({"id": "other-candidate", "speaker_role": "candidate", "text": "I built Python APIs."})
        bank = service.candidate_excerpts(data)
        self.assertEqual([item["turn_id"] for item in bank.values()], ["candidate-uuid", "other-candidate"])
        result = service.materialize("cv_claim_review", selected(["interview-source-2"]), data)
        self.assertEqual(result["claims"][0]["interview_evidence"][0]["turn_id"], "other-candidate")

    def test_identical_reference_names_resolve_only_inside_the_pinned_snapshot(self):
        data = inputs()
        data["prepared"]["turns"][1].update(id="different-snapshot-turn", text="I maintained a Python API.")
        result = service.materialize("cv_claim_review", selected(), data)
        self.assertEqual(result["claims"][0]["interview_evidence"][0]["turn_id"], "different-snapshot-turn")

    def test_no_candidate_speech_cannot_be_replaced_with_interviewer_assertions(self):
        data = inputs(); data["prepared"]["turns"] = data["prepared"]["turns"][:1]
        self.assertEqual(service.candidate_excerpts(data), {})
        field = service.generation_schema("cv_claim_review", data)["$defs"]["ReferencedClaim"]["properties"]["interview_evidence"]
        self.assertEqual(field["maxItems"], 0)
        with self.assertRaisesRegex(ValueError, "CANDIDATE_EVIDENCE_REQUIRED"):
            service.materialize("cv_claim_review", selected([]), data)

    def test_uncertainty_absence_and_same_context_guards_are_unchanged(self):
        raw = selected(); raw["claims"][0]["uncertainty"] = "Speech is ambiguous."
        with self.assertRaises(ValidationError):
            service.materialize("cv_claim_review", raw, inputs())
        raw = selected(); raw["claims"][0].update(status="potential_contradiction", same_context=False)
        with self.assertRaisesRegex(ValueError, "CONTEXT_NOT_ESTABLISHED"):
            service.materialize("cv_claim_review", raw, inputs())
        raw = selected([]); raw["claims"][0]["status"] = "not_discussed"
        data = inputs(); data["prepared"]["text_only"] = True
        with self.assertRaisesRegex(ValueError, "ABSENCE_NOT_ESTABLISHED"):
            service.materialize("cv_claim_review", raw, data)

    def test_legacy_quote_validation_reports_precise_failure_without_repairing_text(self):
        turns = {turn["id"]: turn for turn in inputs()["prepared"]["turns"]}
        cases = [("unknown", "test", "TURN_UNKNOWN", "turn_id"),
            ("question-uuid", "Did you build Python APIs?", "SPEAKER_INVALID", "turn_id"),
            ("candidate-uuid", "Yes, I built Python APIs.", "QUOTE_MISMATCH", "quote")]
        for turn_id, quote, code, field in cases:
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, code) as caught:
                service.checked_quotes([{"turn_id": turn_id, "quote": quote}], turns, candidate=True, path="claims.2.interview_evidence")
            diagnostic = service.validation_diagnostic(caught.exception)
            self.assertEqual(diagnostic["field_path"], f"claims.2.interview_evidence.0.{field}")
            self.assertNotIn(quote, json.dumps(diagnostic))


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_uses_reference_diagnostic_and_does_not_persist_rejected_response(self):
        stages = {}
        async def read(_claim, name): return deepcopy(stages.get(name))
        async def write(_claim, name, value): stages[name] = deepcopy(value)
        bad = selected(["PRIVATE INVALID REFERENCE"])
        with patch.object(service.store, "read_stage", side_effect=read), patch.object(service.store, "write_stage", side_effect=write), \
             patch.object(service.store, "finish", AsyncMock()) as finish, \
             patch.object(service, "generate_json", AsyncMock(side_effect=[json.dumps(bad), json.dumps(selected())])) as generate:
            await service.execute({"kind": "cv_claim_review", "inputs": inputs()}, {"model": "test"})
        result = finish.call_args.args[1]
        self.assertEqual(result["status"], "completed_synthetic")
        payload = json.loads(generate.call_args_list[1].args[1])
        self.assertEqual(payload["validation_error"]["code"], "INTERVIEW_EVIDENCE_SOURCE_UNKNOWN")
        self.assertEqual(payload["validation_error"]["field_path"], "claims.0.interview_evidence.0")
        self.assertEqual(payload["previous_response"], bad)
        self.assertEqual(payload["candidate_interview_excerpts"][0]["turn_id"], "candidate-uuid")
        self.assertNotIn("PRIVATE INVALID REFERENCE", json.dumps(stages))
