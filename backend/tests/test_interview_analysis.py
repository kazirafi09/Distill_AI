"""F5 graph and evidence tests. Expected analysis is test-only, never model input."""

import asyncio
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver

from app.integrations.cv_model import ModelExecution
from app.services.interviews import analysis
from app.services.interviews.analysis_schemas import DimensionEvaluation
from app.services.interviews.rubric import DIMENSIONS, DIMENSION_GUIDANCE, dimension_summaries
from app.services.interviews.transcript_preparation import load_fixture
from app.workflows.interview_analysis.graph import build_interview_analysis_graph


FIXTURE = Path(__file__).resolve().parents[2] / "docs/fixtures/interview-analysis/jd-5"


class InterviewAnalysisTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not all((FIXTURE / name).is_file() for name in ("transcript.json", "timeline-ground-truth.json", "expected-analysis.json", "accuracy-references.json", "cv-screening.json")):
            self.skipTest("Optional local synthetic fixtures are not installed.")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.prepared = load_fixture(FIXTURE / "transcript.json", FIXTURE / "timeline-ground-truth.json")
        raw = json.loads((FIXTURE / "transcript.json").read_text(encoding="utf-8"))
        self.expected = json.loads((FIXTURE / "expected-analysis.json").read_text(encoding="utf-8"))
        self.input = {"prepared": self.prepared, "criteria": raw["provenance"]["criteria_snapshot"], "references": [],
                      "model_options": asdict(ModelExecution("ollama", "qwen3:4b")),
                      "model_version": {"provider": "ollama", "model": "qwen3:4b", "model_digest": "test-only"},
                      "output_directory": self.tmp.name, "allow_synthetic": True, "group_limit": 0}
        ordered = sorted(self.prepared["turns"], key=lambda t: (t["session_start_ms"], t["session_end_ms"], t["id"]))
        self.labels = {t["id"]: f"t{i + 1:03}" for i, t in enumerate(ordered)}
        self.grouping = {"groups": [], "excluded_turns": []}
        for group in self.expected["groups"]:
            self.grouping["groups"].append({
                "id": group["id"], "topic": group["topic"],
                "question_turn_ids": [self.labels[t] for t in group["question_turn_ids"]],
                "answer_turn_ids": [self.labels[t] for t in group["answer_turn_ids"]],
                "follow_up_turn_ids": [self.labels[t] for t in group["follow_up_question_turn_ids"]],
                "clarification_turn_ids": [self.labels[t] for t in group["clarification_question_turn_ids"] + group["clarification_response_turn_ids"]],
                "revisit_turn_ids": [self.labels[t] for t in group["revisit_question_turn_ids"]],
                "answer_state": "explicitly_unanswered" if group["id"] == "g7" else "partially_answered" if group["id"] == "g6" else "answered",
            })
        for excluded in self.expected["excluded_turns"]:
            self.grouping["excluded_turns"].append({"turn_id": self.labels[excluded["turn_id"]], "reason": "logistics"})
        self.calls = []

    def turn_grouping(self):
        """Translate the test-only oracle to the compact model contract."""
        assignments = {}
        kinds = {"question_turn_ids": "question", "answer_turn_ids": "answer",
                 "follow_up_turn_ids": "follow_up", "clarification_turn_ids": "clarification",
                 "revisit_turn_ids": "revisit"}
        for group in self.grouping["groups"]:
            for field, kind in kinds.items():
                for label in group[field]:
                    assignments[label] = group["id"] + ":" + kind
        for item in self.grouping["excluded_turns"]:
            assignments[item["turn_id"]] = "exclude:" + item["reason"]
        return {"groups": [{key: group[key] for key in ("id", "topic", "answer_state")}
                           for group in self.grouping["groups"]],
                "assignments": [assignments[label] for label in self.labels.values() if label in assignments]}

    def evaluation(self, payload, dimension):
        group = payload["group"]
        candidate = next(t for t in payload["turns"] if t["id"] in group["answer_turn_ids"])
        self_report = group["id"] == "g1" and dimension == "accuracy"
        return {"dimension": dimension, "score": None if self_report else 3,
                "reason": "SELF_REPORT_UNVERIFIED" if self_report else "ASSESSED",
                "justification": "Self-report is not independently verified." if self_report else "Synthetic test judgment.",
                "evidence": [{"turn_id": candidate["id"], "quote": candidate["text"]}],
                "accuracy_basis": ("not_assessable" if self_report else "model_knowledge") if dimension == "accuracy" else "not_applicable",
                "reference_evidence": []}

    async def generate(self, system, payload, schema, output_error, **kwargs):
        parsed = json.loads(payload)
        self.calls.append((schema["title"], parsed, kwargs))
        if schema["title"] == "TurnGrouping":
            return json.dumps(self.turn_grouping())
        if schema["title"] == "Mappings":
            return json.dumps({"mappings": [{"group_id": g["id"], "criterion_ids": g["criterion_ids"]} for g in self.expected["groups"]]})
        dimension = re.search(r"Evaluate ONLY (\w+)", system).group(1)
        await asyncio.sleep(0)
        return json.dumps(self.evaluation(parsed, dimension))

    async def invoke(self, generator=None, **changes):
        with patch("app.services.interviews.analysis.generate_json", side_effect=generator or self.generate):
            return await build_interview_analysis_graph().ainvoke({**self.input, **changes}, {"recursion_limit": 1000})

    async def test_full_graph_preserves_seven_roots_evidence_and_unasked_requirement(self):
        final = await self.invoke()
        artifact = final["artifact"]
        self.assertEqual(artifact["status"], "completed_synthetic")
        self.assertEqual(artifact["evaluated_groups"], 7)
        self.assertFalse(artifact["publishable"])
        self.assertFalse(artifact["f6_ready"])
        self.assertEqual(artifact["coverage"][-1]["exploration"], "unasked")
        self.assertIsNone(artifact["coverage"][-1]["performance_score"])
        groups = {g["id"]: g for g in artifact["groups"]}
        self.assertEqual(set(groups["g2"]["answer_turn_ids"]), set(self.expected["groups"][1]["answer_turn_ids"]))
        self.assertEqual(len(artifact["excluded_turns"]), len(self.expected["excluded_turns"]))
        turns = {t["id"]: t for t in self.prepared["turns"]}
        for result in artifact["results"]:
            self.assertEqual(set(result["evaluations"]), set(DIMENSIONS))
            for evaluation in result["evaluations"].values():
                for excerpt in evaluation["evidence"]:
                    self.assertEqual(turns[excerpt["turn_id"]]["text"][excerpt["start_char"]:excerpt["end_char"]], excerpt["quote"])
        unanswered = next(r for r in artifact["results"] if r["group"]["id"] == "g7")
        self.assertEqual(unanswered["evaluations"]["completeness"]["score"], 0)
        self.assertTrue(all(unanswered["evaluations"][d]["score"] is None for d in ("relevance", "accuracy", "articulation")))
        self.assertEqual(len(self.calls), 26)  # Grouping + mapping + six answered groups * four.
        self.assertTrue((Path(final["run_directory"]) / "analysis.json").is_file())

    async def test_parallel_nodes_start_before_merge_and_wait_for_slowest(self):
        entered, completed = set(), set()
        barrier = asyncio.Event()

        async def parallel(system, payload, schema, output_error, **kwargs):
            if schema["title"] != "DimensionEvaluation":
                return await self.generate(system, payload, schema, output_error, **kwargs)
            dimension = re.search(r"Evaluate ONLY (\w+)", system).group(1)
            entered.add(dimension)
            if len(entered) == 4:
                barrier.set()
            await asyncio.wait_for(barrier.wait(), 2)
            await asyncio.sleep(0.03 if dimension == "articulation" else 0)
            completed.add(dimension)
            return json.dumps(self.evaluation(json.loads(payload), dimension))

        final = await self.invoke(parallel, group_limit=1)
        self.assertEqual(entered, set(DIMENSIONS))
        self.assertEqual(completed, set(DIMENSIONS))
        self.assertEqual(len(final["results"]), 1)
        self.assertEqual(final["artifact"]["status"], "partial_synthetic")

    async def test_saved_stage_replay_does_not_repeat_model_calls(self):
        first = await self.invoke()
        self.calls.clear()
        second = await self.invoke()
        self.assertEqual(first["artifact"], second["artifact"])
        self.assertEqual(self.calls, [])

    async def test_invalid_branch_is_repaired_once_and_never_merged_as_complete(self):
        attempts = 0

        async def broken(system, payload, schema, output_error, **kwargs):
            nonlocal attempts
            if "Evaluate ONLY relevance" in system:
                attempts += 1
                value = self.evaluation(json.loads(payload), "relevance")
                value["evidence"][0]["quote"] = "a fabricated quote"
                return json.dumps(value)
            return await self.generate(system, payload, schema, output_error, **kwargs)

        final = await self.invoke(broken)
        self.assertEqual(attempts, 2)
        self.assertEqual(final["artifact"]["status"], "needs_review")
        self.assertEqual(final["results"], [])
        await self.invoke(broken)
        self.assertEqual(attempts, 2)  # Repair budget survives a fresh graph invocation.

    async def test_group_coverage_and_role_errors_withhold_evaluation(self):
        self.grouping["groups"][0]["answer_turn_ids"].pop()
        final = await self.invoke()
        self.assertEqual(final["artifact"]["status"], "needs_review")
        self.assertTrue(all(call[0] == "TurnGrouping" for call in self.calls))

    async def test_excluded_candidate_response_to_revisit_requires_review(self):
        group = next(g for g in self.grouping["groups"] if g["id"] == "g2")
        revisited = self.expected["groups"][1]["revisit_answer_turn_ids"]
        for tid in revisited:
            label = self.labels[tid]
            group["answer_turn_ids"].remove(label)
            self.grouping["excluded_turns"].append({"turn_id": label, "reason": "social"})
        final = await self.invoke()
        self.assertEqual(final["artifact"]["status"], "needs_review")
        self.assertTrue(all(call[0] == "TurnGrouping" for call in self.calls))

    async def test_grouping_repair_receives_assignment_count_diagnostics(self):
        attempts = 0

        async def repairable(system, payload, schema, output_error, **kwargs):
            nonlocal attempts
            if schema["title"] == "TurnGrouping":
                attempts += 1
                if attempts == 1:
                    bad = self.turn_grouping()
                    bad["assignments"].pop()
                    return json.dumps(bad)
                self.assertIn("expected_assignment_count", system)
                self.assertIn("actual_assignment_count", system)
                self.assertIn("previous_invalid_output", json.loads(payload))
            return await self.generate(system, payload, schema, output_error, **kwargs)

        final = await self.invoke(repairable, group_limit=1)
        self.assertEqual(attempts, 2)
        self.assertEqual(final["artifact"]["status"], "partial_synthetic")

    async def test_invalid_assignment_and_wrong_role_are_rejected(self):
        for assignment in ("g999:answer", "g1:unknown", "exclude:unknown", "g1:answer,g2:answer", "g1:answer"):
            with self.subTest(assignment=assignment):
                async def broken(system, payload, schema, output_error, **kwargs):
                    if schema["title"] == "TurnGrouping":
                        value = self.turn_grouping()
                        value["assignments"][0] = assignment  # First turn is recruiter speech.
                        return json.dumps(value)
                    return await self.generate(system, payload, schema, output_error, **kwargs)
                final = await self.invoke(broken, model_version={"case": assignment})
                self.assertEqual(final["artifact"]["status"], "needs_review")
                self.assertEqual(final["results"], [])

    async def test_revisit_answer_cannot_be_assigned_to_another_root(self):
        group = self.grouping["groups"][1]
        label = self.labels[self.expected["groups"][1]["revisit_answer_turn_ids"][0]]
        group["answer_turn_ids"].remove(label)
        self.grouping["groups"][0]["answer_turn_ids"].append(label)
        final = await self.invoke()
        self.assertEqual(final["artifact"]["status"], "needs_review")
        self.assertFalse(final["results"])

    async def test_rootless_revisit_repair_identifies_the_group_and_turns(self):
        attempts = 0

        async def repairable(system, payload, schema, output_error, **kwargs):
            nonlocal attempts
            if schema["title"] == "TurnGrouping":
                attempts += 1
                if attempts == 1:
                    value = self.turn_grouping()
                    value["groups"].append({"id": "g8", "topic": "Duplicate revisit", "answer_state": "answered"})
                    value["assignments"] = ["g8:revisit" if a == "g2:revisit" else a for a in value["assignments"]]
                    return json.dumps(value)
                self.assertIn("GROUP_ROOT_MISSING", system)
                self.assertIn('"group_id": "g8"', system)
                self.assertIn("revisit_turn_ids", system)
            return await self.generate(system, payload, schema, output_error, **kwargs)

        final = await self.invoke(repairable, group_limit=1)
        self.assertEqual(attempts, 2)
        self.assertEqual(final["artifact"]["status"], "partial_synthetic")

    async def test_synthetic_opt_in_and_digest_are_required(self):
        final = await self.invoke(allow_synthetic=False)
        self.assertEqual(final["error_code"], "SYNTHETIC_DEVELOPMENT_INPUT_REQUIRED")
        self.assertEqual(self.calls, [])
        changed = deepcopy(self.prepared)
        changed["turns"][0]["text"] = "changed transcript"
        final = await self.invoke(prepared=changed)
        self.assertEqual(final["error_code"], "TRANSCRIPT_DIGEST_OR_SIZE_INVALID")

    async def test_interrupted_question_without_answer_is_null_not_zero(self):
        state = {**self.input, **analysis.prepare_input(self.input)}
        state["groups"] = [{"id": "g1", "answer_state": "interrupted", "answer_turn_ids": []}]
        for dimension in DIMENSIONS:
            result = await analysis.evaluate_dimension(state, dimension)
            self.assertEqual(result["status"], "validated")
            self.assertIsNone(result["result"]["score"])
            self.assertEqual(result["result"]["evidence"], [])

    async def test_invented_mapping_is_rejected(self):
        self.expected["groups"][0]["criterion_ids"] = ["invented-criterion"]
        final = await self.invoke()
        self.assertEqual(final["artifact"]["status"], "needs_review")
        self.assertFalse(any(call[0] == "DimensionEvaluation" for call in self.calls))

    async def test_changed_model_configuration_gets_a_new_manifest(self):
        first = await self.invoke(group_limit=1)
        options = {**self.input["model_options"], "model": "different-test-model"}
        second = await self.invoke(model_options=options, group_limit=1)
        self.assertNotEqual(first["run_id"], second["run_id"])

    async def test_calibration_guidance_reaches_each_branch_with_complete_context(self):
        # This checks prompt wiring/context, not whether a real model judges correctly.
        requests = {}
        references = json.loads((FIXTURE / "accuracy-references.json").read_text(encoding="utf-8"))["references"]

        async def capture(system, payload, schema, output_error, **kwargs):
            parsed = json.loads(payload)
            if schema["title"] == "DimensionEvaluation":
                dimension = re.search(r"Evaluate ONLY (\w+)", system).group(1)
                requests[(parsed["group"]["id"], dimension)] = (system, parsed)
                self.assertIn(DIMENSION_GUIDANCE[dimension], system)
                for other in set(DIMENSIONS) - {dimension}:
                    self.assertNotIn(DIMENSION_GUIDANCE[other], system)
                self.assertEqual(parsed["reviewed_references"], references if dimension == "accuracy" else [])
                self.assertIn("cite those candidate turns as well as the final answer", system)
            return await self.generate(system, payload, schema, output_error, **kwargs)

        final = await self.invoke(capture, references=references)
        self.assertEqual(final["artifact"]["status"], "completed_synthetic")
        self.assertEqual(len(requests), 24)
        for group_id in ("g2", "g3", "g6"):
            for dimension in DIMENSIONS:
                payload = requests[(group_id, dimension)][1]
                group = payload["group"]
                expected_ids = {tid for key, values in group.items() if key.endswith("_turn_ids") for tid in values}
                self.assertEqual({t["id"] for t in payload["turns"]}, expected_ids)
        validation_turns = requests[("g2", "completeness")][1]["turns"]
        self.assertTrue(any("cannot remember the status code" in t["text"] for t in validation_turns))
        self.assertTrue(any("four twenty two" in t["text"] for t in validation_turns))
        query_turns = requests[("g3", "accuracy")][1]["turns"]
        self.assertTrue(any(t["speaker"] == "recruiter" and "Would an index" in t["text"] for t in query_turns))
        test_turns = requests[("g6", "articulation")][1]["turns"]
        self.assertTrue(any("check it was saved" in t["text"] for t in test_turns))
        self.assertTrue(any("unprocess..." in t["text"] for t in test_turns))

    async def test_prompt_revision_does_not_reuse_previous_evaluation_cache(self):
        with patch.object(analysis, "PROMPT_VERSION", "interview-analysis-7"):
            old = await self.invoke(group_limit=1)
        self.calls.clear()
        current = await self.invoke(group_limit=1)
        self.assertNotEqual(old["run_id"], current["run_id"])
        self.assertEqual(len(self.calls), 6)
        old_manifest = json.loads((Path(old["run_directory"]) / "manifest.json").read_text(encoding="utf-8"))
        new_manifest = json.loads((Path(current["run_directory"]) / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(old_manifest.pop("prompt"), "interview-analysis-7")
        self.assertEqual(new_manifest.pop("prompt"), analysis.PROMPT_VERSION)
        self.assertEqual(old_manifest, new_manifest)
        self.assertTrue((Path(old["run_directory"]) / "analysis.json").is_file())

    async def test_candidate_clarification_evidence_repairs_using_allowed_answer_ids(self):
        attempts = 0

        async def repair_scope(system, payload, schema, output_error, **kwargs):
            nonlocal attempts
            parsed = json.loads(payload)
            if schema["title"] == "DimensionEvaluation" and parsed["group"]["id"] == "g4" and "Evaluate ONLY articulation" in system:
                attempts += 1
                value = self.evaluation(parsed, "articulation")
                if attempts == 1:
                    clarification = next(t for t in parsed["turns"]
                                         if t["speaker"] == "candidate" and t["id"] in parsed["group"]["clarification_turn_ids"])
                    value["evidence"] = [{"turn_id": clarification["id"], "quote": clarification["text"]}]
                else:
                    self.assertIn("EVIDENCE_SCOPE_INVALID", system)
                    self.assertIn('"allowed_answer_turn_ids"', system)
                    self.assertIn('"invalid_turn_id"', system)
                    self.assertIn("previous_invalid_output", parsed)
                    for turn_id in parsed["group"]["answer_turn_ids"]:
                        self.assertIn(turn_id, system)
                return json.dumps(value)
            return await self.generate(system, payload, schema, output_error, **kwargs)

        final = await self.invoke(repair_scope)
        self.assertEqual(attempts, 2)
        self.assertEqual(final["artifact"]["status"], "completed_synthetic")
        result = next(r for r in final["results"] if r["group"]["id"] == "g4")
        for excerpt in result["evaluations"]["articulation"]["evidence"]:
            self.assertIn(excerpt["turn_id"], result["group"]["answer_turn_ids"])

    async def test_expected_analysis_and_timing_labels_are_not_model_inputs(self):
        await self.invoke(group_limit=1)
        for title, payload, options in self.calls:
            encoded = json.dumps(payload)
            for key in ("expected_dimensions", "evidence_examples", "expected_session_start_ms", "interaction_cases"):
                self.assertNotIn(key, encoded)
            self.assertEqual(options["execution"].provider, "ollama")

    async def test_supplied_checkpointer_can_store_graph_state(self):
        with patch("app.services.interviews.analysis.generate_json", side_effect=self.generate):
            graph = build_interview_analysis_graph(InMemorySaver())
            config = {"configurable": {"thread_id": "f5-test"}, "recursion_limit": 1000}
            final = await graph.ainvoke({**self.input, "group_limit": 1}, config)
            snapshot = await graph.aget_state(config)
        self.assertEqual(snapshot.values["artifact"], final["artifact"])
        self.assertFalse(snapshot.next)


class EvidenceAndRubricTests(unittest.TestCase):
    def setUp(self):
        if not all((FIXTURE / name).is_file() for name in ("transcript.json", "timeline-ground-truth.json")):
            self.skipTest("Optional local synthetic fixtures are not installed.")
        self.prepared = load_fixture(FIXTURE / "transcript.json", FIXTURE / "timeline-ground-truth.json")
        self.turn = next(t for t in self.prepared["turns"] if t["speaker_role"] == "candidate")
        self.group = {"answer_turn_ids": [self.turn["id"]]}
        self.state = {"prepared": self.prepared, "turns": self.prepared["turns"], "references": []}
        self.raw = {"dimension": "accuracy", "score": 2, "reason": "ASSESSED", "justification": "Test judgment.",
                    "evidence": [{"turn_id": self.turn["id"], "quote": self.turn["text"]}],
                    "accuracy_basis": "model_knowledge", "reference_evidence": []}

    def test_exact_sources_and_dimension_must_match(self):
        raw = deepcopy(self.raw)
        raw["evidence"][0]["turn_id"] = self.prepared["turns"][0]["id"]
        with self.assertRaisesRegex(analysis.AnalysisError, "EVIDENCE_SCOPE_INVALID"):
            analysis.materialize_evaluation(self.state, self.group, "accuracy", raw)
        with self.assertRaisesRegex(analysis.AnalysisError, "EVALUATION_DIMENSION_INVALID"):
            analysis.materialize_evaluation(self.state, self.group, "relevance", self.raw)

    def test_accuracy_basis_and_reference_spans_are_checked(self):
        self.state["references"] = [{"id": "ref1", "text": "Reviewed reference text.", "url": "https://example.test/ref", "version": "1"}]
        self.raw.update(accuracy_basis="mixed", reference_evidence=[{"reference_id": "ref1", "quote": "reference text"}])
        value = analysis.materialize_evaluation(self.state, self.group, "accuracy", self.raw)
        self.assertEqual(value["score_100"], 50)
        self.assertEqual(value["reference_evidence"][0]["start_char"], 9)
        self.assertEqual(value["accuracy_warning"], "MODEL_KNOWLEDGE_UNVERIFIED")
        self.raw["reference_evidence"][0]["reference_id"] = "invented-reference"
        with self.assertRaisesRegex(analysis.AnalysisError, "REFERENCE_QUOTE_INVALID"):
            analysis.materialize_evaluation(self.state, self.group, "accuracy", self.raw)

    def test_invalid_scores_and_null_reasons_are_rejected(self):
        for value in (True, 1.5, 5, -1, None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                DimensionEvaluation.model_validate({**self.raw, "score": value})
        with self.assertRaises(ValueError):
            DimensionEvaluation.model_validate({**self.raw, "accuracy_basis": "reference_supported"})
        with self.assertRaises(ValueError):
            DimensionEvaluation.model_validate({**self.raw, "evidence": []})

    def test_nulls_are_excluded_from_denominator_and_zero_is_retained(self):
        results = [{"evaluations": {d: {"score": value} for d in DIMENSIONS}} for value in (4, None, 0)]
        for value in dimension_summaries(results).values():
            self.assertEqual(value["score_100"], "50.00")
            self.assertEqual(value["assessed_count"], 2)
            self.assertEqual(value["not_assessable_count"], 1)
        self.assertIsNone(dimension_summaries([])["accuracy"]["score_100"])


if __name__ == "__main__":
    unittest.main()
