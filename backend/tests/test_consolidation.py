"""F6 topology, frozen components, citations and persisted two-repair boundary."""
import asyncio
import json
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from app.services.interviews import analysis, consolidation as service
from app.services.interviews.consolidation_runs import synthetic_cv
from app.workflows.assessment_consolidation.graph import build_consolidation_graph
import test_interview_analysis as f5_fixtures


class ConsolidationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.f5 = f5_fixtures.InterviewAnalysisTests()
        await self.f5.asyncSetUp()
        self.addCleanup(self.f5.tmp.cleanup)
        output = await self.f5.invoke()
        self.inputs = {"prepared": self.f5.prepared, "criteria": self.f5.input["criteria"], "references": [],
            "profile_snapshot": {"criteria": self.f5.input["criteria"]}, "profile_version": 1,
            "parent_run_id": str(uuid4()), "f5": output["artifact"], "model_options": {
                **self.f5.input["model_options"], "context_tokens": 65536, "output_tokens": 8192}}
        self.inputs["cv"] = synthetic_cv(SimpleNamespace(inputs=self.inputs))
        self.state = {"storage_run_id": str(uuid4()), "storage_token": str(uuid4()), "inputs": self.inputs,
            "model_version": {"model": "test-only", "model_digest": "test-fixed"}}
        self.sources = service.build_sources(self.inputs)
        self.cache, self.calls = {}, []
        for target, replacement in (("app.services.interviews.analysis.stage_read", self.read),
                                    ("app.services.interviews.analysis.stage_write", self.write)):
            mocked = patch(target, side_effect=replacement)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.persist = patch("app.services.interviews.assessment_store.finish", new_callable=AsyncMock)
        self.saved = self.persist.start()
        self.addCleanup(self.persist.stop)

    async def read(self, state, name):
        return deepcopy(self.cache.get(name))

    async def write(self, state, name, payload):
        self.cache[name] = deepcopy(payload)

    def findings(self, section):
        source = next(s for s in self.sources if s["criterion_ids"] and service.supports(section, s))
        return [{"criterion_id": source["criterion_ids"][0], "text": f"Synthetic test finding for {section}.", "source_ids": [source["id"]]}]

    def draft(self):
        return {**{section: self.findings(section) for section in service.BRANCHES}, "suitability": {
            "position": "mixed_evidence", "rationale": "Synthetic test rationale, with limitations.", "source_ids": ["cv-1"]}}

    async def generate(self, system, payload, schema, *args, **kwargs):
        self.calls.append((system, payload, schema))
        if schema["title"] == "Findings":
            section = next(key for key in service.BRANCHES if service.INSTRUCTIONS[key] in system)
            return json.dumps({"findings": self.findings(section)})
        if schema["title"] == "Suitability":
            return json.dumps(self.draft()["suitability"])
        return json.dumps(self.draft())

    async def invoke(self, generator=None):
        with patch.object(service, "generate_json", side_effect=generator or self.generate):
            return await build_consolidation_graph().ainvoke(deepcopy(self.state), {"recursion_limit": 100})

    async def test_graph_preserves_components_and_all_four_sections(self):
        result = await self.invoke()
        artifact = result["artifact"]
        self.assertEqual(artifact["status"], "completed_synthetic")
        self.assertEqual(artifact["component_scores"]["cv"], self.inputs["cv"]["score"])
        self.assertEqual(artifact["component_scores"]["interview"], self.inputs["f5"]["dimension_summaries"])
        self.assertEqual(set(artifact["findings"]), {*service.BRANCHES, "suitability"})
        self.assertEqual(len(self.calls), 5)
        self.assertFalse(artifact["publishable"])
        self.saved.assert_awaited_once()

    async def test_four_branches_are_parallel_and_merge_waits_for_all(self):
        entered, completed = set(), set()
        barrier = asyncio.Event()

        async def generate(system, payload, schema, *args, **kwargs):
            if schema["title"] == "Findings":
                section = next(key for key in service.BRANCHES if service.INSTRUCTIONS[key] in system)
                entered.add(section)
                if len(entered) == 4:
                    barrier.set()
                await asyncio.wait_for(barrier.wait(), 2)
                if section == "recommended_areas":
                    await asyncio.sleep(.03)
                completed.add(section)
            elif schema["title"] == "Suitability":
                self.assertEqual(completed, set(service.BRANCHES))
            return await self.generate(system, payload, schema, *args, **kwargs)

        await self.invoke(generate)
        self.assertEqual(entered, set(service.BRANCHES))

    async def test_invalid_traceability_repairs_twice_then_withholds_report(self):
        async def bad(system, payload, schema, *args, **kwargs):
            raw = await self.generate(system, payload, schema, *args, **kwargs)
            value = json.loads(raw)
            if schema["title"] == "Findings":
                value["findings"][0]["source_ids"] = ["invented-source"]
            elif schema["title"] == "AssessmentDraft":
                value["strengths"][0]["source_ids"] = ["invented-source"]
            return json.dumps(value)

        result = await self.invoke(bad)
        self.assertEqual(result["artifact"]["status"], "needs_review")
        self.assertEqual(result["artifact"]["provenance"]["repairs_used"], 2)
        self.assertNotIn("findings", result["artifact"])
        self.assertIn("f6-repair-1", self.cache)
        self.assertIn("f6-repair-2", self.cache)
        count = len(self.calls)
        await self.invoke(bad)
        self.assertEqual(len(self.calls), count)

    async def test_invalid_first_draft_can_be_repaired_without_repeating_branches(self):
        async def bad_branch(system, payload, schema, *args, **kwargs):
            raw = await self.generate(system, payload, schema, *args, **kwargs)
            return '{}' if schema["title"] == "Findings" and service.INSTRUCTIONS["weaknesses"] in system else raw

        result = await self.invoke(bad_branch)
        self.assertEqual(result["artifact"]["status"], "completed_synthetic")
        self.assertEqual(result["artifact"]["provenance"]["repairs_used"], 1)
        self.assertEqual(len(self.calls), 5)  # Four branches plus one whole-assessment repair.

    async def test_committed_model_stages_survive_final_persistence_failure(self):
        self.saved.side_effect = RuntimeError("simulated process failure")
        with self.assertRaises(RuntimeError):
            await self.invoke()
        self.saved.side_effect = None
        result = await self.invoke()
        self.assertEqual(result["artifact"]["status"], "completed_synthetic")
        self.assertEqual(len(self.calls), 5)

    async def test_provider_failure_keeps_completed_parallel_branches(self):
        failed = False

        async def transient(system, payload, schema, *args, **kwargs):
            nonlocal failed
            if service.INSTRUCTIONS["weaknesses"] in system and not failed:
                failed = True
                raise ValueError("MODEL_NOT_AVAILABLE")
            return await self.generate(system, payload, schema, *args, **kwargs)

        first = await self.invoke(transient)
        self.assertEqual(first["artifact"]["status"], "failed")
        second = await self.invoke()
        self.assertEqual(second["artifact"]["status"], "completed_synthetic")
        self.assertEqual(len(self.calls), 5)  # Five successful calls, no successful branch repeated.

    async def test_source_tampering_and_changed_scores_are_rejected(self):
        bad = deepcopy(self.inputs)
        bad["cv"]["matches"][0]["evidence"][0]["quote"] = "invented quote"
        with self.assertRaisesRegex(analysis.AnalysisError, "F6_CV_EVIDENCE_INVALID"):
            service.build_sources(bad)
        bad = deepcopy(self.inputs)
        bad["f5"]["results"][0]["evaluations"]["relevance"]["score_100"] = 0
        with self.assertRaises(analysis.AnalysisError):
            service.build_sources(bad)

    async def test_wrong_criterion_and_absence_as_weakness_are_rejected(self):
        state = {**self.state, "sources": self.sources, "draft": self.draft()}
        state["draft"]["strengths"][0]["criterion_id"] = self.inputs["criteria"][-1]["id"]
        with self.assertRaisesRegex(analysis.AnalysisError, "F6_CITATION_INVALID"):
            service.validate_draft(state)
        state["draft"] = self.draft()
        gap = next(s for s in self.sources if s["kind"] == "coverage" and s["exploration"] == "unasked")
        state["draft"]["weaknesses"] = [{"criterion_id": gap["criterion_ids"][0], "text": "Unsupported failure claim.", "source_ids": [gap["id"]]}]
        with self.assertRaisesRegex(analysis.AnalysisError, "F6_FINDING_SUPPORT_INVALID"):
            service.validate_draft(state)

    async def test_gaps_cannot_lose_all_follow_up_recommendations(self):
        draft = self.draft()
        draft["recommended_areas"] = []
        with self.assertRaisesRegex(analysis.AnalysisError, "F6_VERIFICATION_AREAS_MISSING"):
            service.validate_draft({**self.state, "sources": self.sources, "draft": draft})

    async def test_model_context_preserves_semantics_without_display_provenance(self):
        original = deepcopy(self.sources)
        projected = service.context({**self.state, "sources": self.sources})
        self.assertEqual(self.sources, original)
        self.assertEqual(projected["cv_score"], self.inputs["cv"]["score"])
        self.assertEqual(projected["interview_scores"], self.inputs["f5"]["dimension_summaries"])
        self.assertEqual(len(projected["sources"]), len(original))
        excerpts = {e["id"]: e for e in projected["excerpts"]}
        for source, compact in zip(original, projected["sources"]):
            for key in ("id", "criterion_ids", "kind", "justification", "score", "accuracy_basis", "decision", "reason"):
                if key in source:
                    self.assertEqual(compact[key], source[key])
            self.assertEqual([excerpts[key]["quote"] for key in compact.get("evidence_ids", [])], [e["quote"] for e in source.get("evidence", [])])
            self.assertEqual([excerpts[key]["quote"] for key in compact.get("reference_evidence_ids", [])], [e["quote"] for e in source.get("reference_evidence", [])])
            for key in compact.get("evidence_ids", []):
                excerpt = excerpts[key]
                self.assertNotIn("transcript_id", excerpt)
                self.assertNotIn("start_char", excerpt)
                self.assertNotIn("file_version_id", excerpt)
        self.assertLess(len(json.dumps({"sources": projected["sources"], "excerpts": projected["excerpts"]})), len(json.dumps(original)))
        other_file = deepcopy(original[0])
        other_file["id"] = "cv-another-file"
        other_file["evidence"][0]["file_version_id"] = str(uuid4())
        different_origins = service.context({**self.state, "sources": [original[0], other_file]})
        self.assertNotEqual(different_origins["sources"][0]["evidence_ids"], different_origins["sources"][1]["evidence_ids"])

    async def test_output_schema_only_allows_source_ids_and_repair_has_scope_diagnostics(self):
        from app.services.interviews.consolidation_schemas import AssessmentDraft
        state = {**self.state, "sources": self.sources, "draft": self.draft()}
        schema = service.output_schema(state, AssessmentDraft)
        valid = {s["id"] for s in self.sources}
        for name in ("Finding", "Suitability"):
            allowed = schema["$defs"][name]["properties"]["source_ids"]["items"]["enum"]
            self.assertEqual(set(allowed), valid)
            self.assertFalse(any(key.startswith("excerpt-") for key in allowed))
        state["draft"]["recommended_areas"][0]["source_ids"] = ["excerpt-14"]
        with self.assertRaises(analysis.AnalysisError) as error:
            service.validate_draft(state)
        self.assertEqual(error.exception.details["invalid_source_ids"], ["excerpt-14"])
        self.assertTrue(error.exception.details["allowed_source_ids"])
