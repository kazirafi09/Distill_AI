"""Evidence uncertainty and bounded diagnostics, without provider calls or a database."""
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app.services.interviews import post_interview_analysis as service
from app.services.interviews.performance_rubric import DEFAULT_POLICY, DIMENSIONS


def inputs():
    return {
        "prepared": {"synthetic": False, "text_only": True, "aligned_content_sha256": "transcript",
            "source_quality_flags": ["TRANSCRIPT_INCOMPLETE", "TRANSCRIPTION_FLUSH_FAILED"],
            "turns": [{"id": "t1", "speaker_role": "candidate",
                "text": "A list is immutable, so I can add or remove elements. I use lists for collections that change."}]},
        "criteria": [{"id": "python", "text": "Python"}], "references": [],
        "scoring_settings": {"policy": DEFAULT_POLICY},
        "model_options": {"provider": "gemini", "model": "test", "context_tokens": 65536},
        "f5": {"artifact_sha256": "f5", "results": []}, "parent_run_id": "parent", "source_key": "key",
        "cv": {"documents": [{"parse_id": "parse", "file_version_id": "file", "text": "Python",
            "sources": [{"id": "page", "start": 0, "end": 6}]}]},
    }


def performance():
    return {"dimensions": [{"dimension": name, "score": 2, "reason": "assessed",
        "justification": "Limited demonstrated depth in the supplied answer.", "uncertainty": None,
        "evidence": [{"turn_id": "t1", "quote": "I use lists for collections that change."}],
        "basis": "model_knowledge" if name == "technical_competency" else "interview_only",
        "reference_evidence": []} for name in DIMENSIONS]}


class EvidenceTests(unittest.TestCase):
    def test_material_ambiguity_cannot_receive_a_score(self):
        raw = performance()
        technical = raw["dimensions"][0]
        technical.update(uncertainty="The mutability word conflicts with the described operations.",
                         reason="transcript_uncertain")
        with self.assertRaises(ValidationError):
            service.materialize("interview_performance", raw, inputs())
        technical.update(score=None, basis="not_assessed")
        result = service.materialize("interview_performance", raw, inputs())
        self.assertIsNone(result["summary"]["overall_score_100"])
        self.assertEqual(result["summary"]["missing_weight"], 25)
        self.assertEqual(result["dimensions"]["technical_competency"]["reason"], "transcript_uncertain")

    def test_gapped_source_does_not_invalidate_clear_attributable_answers(self):
        result = service.materialize("interview_performance", performance(), inputs())
        self.assertEqual(result["summary"]["overall_score_100"], "50.00")

    def test_technical_knowledge_basis_requires_reference_or_model_label(self):
        raw = performance()
        raw["dimensions"][0]["basis"] = "interview_only"
        with self.assertRaisesRegex(ValueError, "TECHNICAL_BASIS_INVALID"):
            service.materialize("interview_performance", raw, inputs())
        raw["dimensions"][0]["basis"] = "saved_reference_and_interview"
        with self.assertRaisesRegex(ValueError, "REFERENCE_BASIS_INVALID"):
            service.materialize("interview_performance", raw, inputs())
        data = inputs()
        data["references"] = [{"id": "ref", "text": "Lists are mutable.", "document_type": "technical",
                                "version": "1", "url": None}]
        raw["dimensions"][0]["reference_evidence"] = [{"reference_id": "ref", "quote": "Lists are mutable."}]
        result = service.materialize("interview_performance", raw, data)
        self.assertEqual(result["dimensions"]["technical_competency"]["basis"], "saved_reference_and_interview")

    def test_ambiguous_cv_claim_cannot_be_a_contradiction(self):
        raw = {"claim": "Uses Python", "criterion_ids": ["python"], "cv_evidence": {
            "parse_id": "parse", "file_version_id": "file", "source_id": "page", "start": 0, "end": 6, "quote": "Python"},
            "status": "potential_contradiction", "explanation": "Unclear wording.", "interview_evidence": [],
            "same_context": True, "scope_comparison": "Same role.", "follow_up": "Clarify the wording.",
            "uncertainty": "The saved answer is ambiguous."}
        with self.assertRaises(ValidationError):
            service.Claim.model_validate(raw)
        raw["status"] = "unable_to_assess"
        service.Claim.model_validate(raw)

    def test_gapped_transcript_cannot_establish_not_discussed(self):
        claim = {"claim": "Uses Python", "criterion_ids": ["python"], "cv_evidence": {
            "parse_id": "parse", "file_version_id": "file", "source_id": "page", "start": 0, "end": 6, "quote": "Python"},
            "status": "not_discussed", "explanation": "No matching discussion.", "interview_evidence": [],
            "same_context": False, "scope_comparison": "No context.", "follow_up": "Ask about Python.", "uncertainty": None}
        data = inputs()
        data["prepared"]["text_only"] = False
        with self.assertRaisesRegex(ValueError, "ABSENCE_NOT_ESTABLISHED"):
            service.materialize("cv_claim_review", {"claims": [claim], "selection_note": "One claim."}, data)

    def test_source_warnings_distinguish_timing_and_capture_gaps(self):
        self.assertEqual(len(service.source_warnings(inputs()["prepared"])), 2)
        self.assertEqual(service.source_warnings({"text_only": False, "source_quality_flags": []}), [])
        warnings = service.source_warnings({"source_quality_flags": ["TRANSCRIPT_INCOMPLETE"]})
        self.assertEqual(len(warnings), 1)
        self.assertIn("omit speech", warnings[0])


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def execute_with(self, responses, data=None, stages=None):
        self.stages = deepcopy(stages or {})

        async def read(_claim, name):
            return deepcopy(self.stages.get(name))

        async def write(_claim, name, value):
            self.stages[name] = deepcopy(value)

        with patch.object(service.store, "read_stage", side_effect=read), \
                patch.object(service.store, "write_stage", side_effect=write), \
                patch.object(service.store, "finish", new_callable=AsyncMock) as finish, \
                patch.object(service, "generate_json", side_effect=responses) as generate:
            await service.execute({"kind": "interview_performance", "inputs": data or inputs()}, {"model": "test"})
            return finish.call_args.args[1], generate.call_args_list

    async def test_repair_receives_safe_diagnostic_and_retains_source_policy(self):
        bad = performance()
        bad["dimensions"][0]["basis"] = "interview_only"
        result, calls = await self.execute_with([json.dumps(bad), json.dumps(performance())])
        diagnostic = self.stages["extension-attempts"]["errors"][0]
        self.assertEqual(diagnostic["code"], "INTERVIEW_PERFORMANCE_TECHNICAL_BASIS_INVALID")
        self.assertEqual(diagnostic["field_path"], "dimensions.basis")
        repair = json.loads(calls[1].args[1])
        self.assertEqual(repair["validation_error"], diagnostic)
        self.assertIn("cross-speaker order", calls[0].args[0])
        self.assertEqual(calls[0].args[0], calls[1].args[0])
        self.assertEqual(result["warnings"][:-1], service.source_warnings(inputs()["prepared"]))
        self.assertEqual(result["status"], "completed")

    async def test_failed_attempts_preserve_paths_without_raw_model_text(self):
        bad = performance()
        bad["dimensions"][0]["score"] = "PRIVATE MODEL OUTPUT"
        result, _ = await self.execute_with([json.dumps(bad), json.dumps(bad)])
        errors = self.stages["extension-attempts"]["errors"]
        self.assertEqual([error["attempt"] for error in errors], [1, 2])
        self.assertEqual(errors[0]["field_path"], "dimensions.0.score")
        self.assertNotIn("PRIVATE MODEL OUTPUT", json.dumps(self.stages))
        self.assertEqual(result["error_code"], "EXTENSION_OUTPUT_NEEDS_REVIEW")
        self.assertFalse(result["publishable"])

    async def test_unexpected_exception_text_is_not_persisted(self):
        result, _ = await self.execute_with([ValueError("PRIVATE MODEL OUTPUT"), ValueError("PRIVATE MODEL OUTPUT")])
        self.assertEqual(result["status"], "needs_review")
        self.assertNotIn("PRIVATE MODEL OUTPUT", json.dumps(self.stages))
        self.assertEqual(self.stages["extension-attempts"]["errors"][0]["code"], "EXTENSION_OUTPUT_INVALID")

    async def test_resumed_repair_uses_persisted_diagnostic(self):
        diagnostic = {"attempt": 1, **service.validation_diagnostic(ValueError("EXTENSION_EVIDENCE_INVALID"))}
        result, calls = await self.execute_with([json.dumps(performance())],
            stages={"extension-attempts": {"count": 1, "errors": [diagnostic]}})
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(calls[0].args[1])["validation_error"], diagnostic)
        self.assertEqual(result["status"], "completed")
