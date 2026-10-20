"""Offline preparation checks; no settings, database, media or model imports."""

from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from uuid import uuid4

from app.scripts.prepare_interview_transcript import main
from app.services.interviews.transcript_preparation import (
    MAX_TIME_MS, PreparationError, align_segments, load_fixture,
)


FIXTURE = Path(__file__).resolve().parents[2] / "docs/fixtures/interview-analysis/jd-5"


class TranscriptPreparationTests(unittest.TestCase):
    def setUp(self):
        if not all((FIXTURE / name).is_file() for name in ("transcript.json", "timeline-ground-truth.json")):
            self.skipTest("Optional local synthetic fixtures are not installed.")
        self.raw = json.loads((FIXTURE / "transcript.json").read_text(encoding="utf-8"))
        self.timeline = json.loads((FIXTURE / "timeline-ground-truth.json").read_text(encoding="utf-8"))
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.transcript_path = Path(self.tmp.name) / "transcript.json"
        self.timeline_path = Path(self.tmp.name) / "timeline.json"

    def load(self):
        self.transcript_path.write_text(json.dumps(self.raw), encoding="utf-8")
        self.timeline_path.write_text(json.dumps(self.timeline), encoding="utf-8")
        return load_fixture(self.transcript_path, self.timeline_path)

    def assert_code(self, code):
        with self.assertRaises(PreparationError) as caught:
            self.load()
        self.assertEqual(str(caught.exception), code)

    def test_realistic_fixture_aligns_all_turns_and_preserves_sources(self):
        raw_before, timeline_before = deepcopy(self.raw), deepcopy(self.timeline)
        preview = self.load()
        self.assertEqual(len(preview["turns"]), 81)
        actual = {turn["id"]: turn for turn in preview["turns"]}
        for expected in self.timeline["turns"]:
            turn = actual[expected["turn_id"]]
            self.assertEqual(turn["session_start_ms"], expected["expected_session_start_ms"])
            self.assertEqual(turn["session_end_ms"], expected["expected_session_end_ms"])
        for source in self.raw["segments"]:
            for key in source.keys() - {"session_start_ms", "session_end_ms"}:
                if key == "created_at":
                    self.assertEqual(datetime.fromisoformat(actual[source["id"]][key]),
                                     datetime.fromisoformat(source[key]))
                else:
                    self.assertEqual(actual[source["id"]][key], source[key])
        self.assertEqual(self.raw, raw_before)
        self.assertEqual(self.timeline, timeline_before)
        self.assertFalse(preview["f5_ready"])
        self.assertEqual(preview["source_status"], "needs_review")
        self.assertIn("FINALIZATION_PENDING", preview["source_quality_flags"])

    def test_overlap_and_delayed_delivery_do_not_change_speech_order(self):
        preview = self.load()
        actual = {turn["id"]: turn for turn in preview["turns"]}
        for overlap in self.timeline["overlaps"]:
            turns = [actual[tid] for tid in overlap["turn_ids"]]
            self.assertEqual(len({turn["speaker_role"] for turn in turns}), 2)
            self.assertLess(max(turn["session_start_ms"] for turn in turns),
                            min(turn["session_end_ms"] for turn in turns))
        case = self.timeline["arrival_order_case"]
        ids = [turn["id"] for turn in preview["turns"]]
        self.assertLess(ids.index(case["earlier_spoken_turn_id"]), ids.index(case["later_spoken_turn_id"]))
        self.assertGreater(actual[case["earlier_spoken_turn_id"]]["created_at"],
                           actual[case["later_spoken_turn_id"]]["created_at"])

    def test_reconnect_clock_reset_uses_its_own_origin(self):
        preview = self.load()
        stream = self.timeline["streams"][-1]
        turns = [turn for turn in preview["turns"] if turn["stream_id"] == stream["stream_id"]]
        self.assertTrue(turns)
        for turn in turns:
            self.assertEqual(turn["session_start_ms"], stream["stream_origin_ms"] + turn["stream_start_ms"])

    def test_expected_turn_times_and_analysis_labels_are_not_used(self):
        baseline = self.load()
        self.timeline["turns"] = [{"expected_session_start_ms": -999, "answer": "oracle-secret"}]
        self.timeline["overlaps"] = [{"label": "oracle-secret"}]
        self.timeline["expected_analysis"] = {"groups": "oracle-secret"}
        self.raw["expected_analysis"] = "oracle-secret"
        result = self.load()
        self.assertEqual(result, baseline)
        self.assertNotIn("oracle-secret", json.dumps(result))

    def test_save_order_does_not_change_alignment_or_digests(self):
        baseline = self.load()
        self.raw["segments"].reverse()
        self.timeline["streams"].reverse()
        self.assertEqual(self.load(), baseline)

    def test_missing_stream_timing_never_falls_back_to_save_time(self):
        self.timeline["streams"].pop()
        self.assert_code("STREAM_TIMING_MISSING")

    def test_duplicate_and_unused_offsets_are_rejected(self):
        original = deepcopy(self.timeline["streams"])
        self.timeline["streams"].append(deepcopy(original[0]))
        self.assert_code("DUPLICATE_STREAM_TIMING")
        self.timeline["streams"] = original + [{**original[0], "stream_id": str(uuid4())}]
        self.assert_code("UNUSED_STREAM_TIMING")

    def test_cross_transcript_segment_is_rejected(self):
        self.raw["segments"][0]["transcript_id"] = str(uuid4())
        self.assert_code("TRANSCRIPT_SCOPE_MISMATCH")

    def test_duplicate_id_or_event_is_rejected(self):
        source = deepcopy(self.raw["segments"][0])
        self.raw["segments"].append(source)
        self.assert_code("DUPLICATE_SOURCE_SEGMENT")
        source["id"] = str(uuid4())
        self.assert_code("DUPLICATE_SOURCE_SEGMENT")

    def test_speaker_and_identity_conflicts_are_rejected(self):
        self.timeline["streams"][0]["speaker_role"] = "candidate"
        self.assert_code("TRANSCRIPT_SPEAKER_MISMATCH")
        self.timeline["streams"][0]["speaker_role"] = "recruiter"
        role = self.raw["segments"][0]["speaker_role"]
        next(row for row in self.raw["segments"][1:] if row["speaker_role"] == role)["participant_identity"] = "other-participant"
        self.assert_code("TRANSCRIPT_SPEAKER_MISMATCH")

    def test_existing_session_times_cannot_be_silently_replaced(self):
        self.raw["segments"][0].update(session_start_ms=1000, session_end_ms=2000)
        self.assert_code("SESSION_TIMING_CONFLICT")

    def test_invalid_segment_errors_do_not_expose_transcript(self):
        source = deepcopy(self.raw["segments"][0])
        invalid_values = [
            {"stream_start_ms": True}, {"stream_start_ms": -1},
            {"stream_start_ms": 0.5}, {"stream_end_ms": -1},
            {"stream_end_ms": source["stream_start_ms"] - 1},
            {"source_event_key": "incorrect-key"}, {"session_start_ms": 1},
            {"text": " "}, {"unexpected": "field"},
        ]
        for changes in invalid_values:
            with self.subTest(changes=changes):
                self.raw["segments"][0] = {**source, "text": "private-input-marker", **changes}
                self.assert_code("TRANSCRIPT_INPUT_INVALID")

    def test_time_overflow_is_rejected(self):
        self.timeline["streams"][0]["stream_origin_ms"] = MAX_TIME_MS
        self.assert_code("TRANSCRIPT_TIME_OVERFLOW")

    def test_empty_and_unbounded_segment_sets_are_rejected(self):
        with self.assertRaisesRegex(PreparationError, "TRANSCRIPT_SEGMENT_COUNT_INVALID"):
            align_segments(self.raw["transcript"]["id"], [], self.timeline["streams"])
        with self.assertRaisesRegex(PreparationError, "TRANSCRIPT_SEGMENT_COUNT_INVALID"):
            align_segments(self.raw["transcript"]["id"], [None] * 20001, self.timeline["streams"])

    def test_synthetic_identity_and_revision_are_required(self):
        self.raw["synthetic"] = False
        self.assert_code("SYNTHETIC_FIXTURE_REQUIRED")
        self.raw["synthetic"] = True
        self.timeline["fixture_revision"] += 1
        self.assert_code("FIXTURE_VERSION_MISMATCH")
        self.timeline["fixture_revision"] = self.raw["fixture_revision"]
        self.timeline["fixture_id"] = "another-fixture"
        self.assert_code("FIXTURE_VERSION_MISMATCH")

    def test_save_time_based_offset_and_wrong_units_are_rejected(self):
        self.timeline["time_unit"] = "seconds"
        self.assert_code("TIMELINE_CONTRACT_INVALID")
        self.timeline["time_unit"] = "milliseconds"
        self.timeline["streams"][0]["origin_source"] = "inferred_from_created_at"
        self.assert_code("TRANSCRIPT_INPUT_INVALID")

    def test_completed_source_is_still_a_preview(self):
        self.raw["transcript"]["status"] = "completed"
        result = self.load()
        self.assertEqual(result["status"], "synthetic_preview")
        self.assertFalse(result["f5_ready"])

    def test_cli_summary_write_and_overwrite_protection(self):
        self.load()
        args = ["--transcript", str(self.transcript_path), "--timeline", str(self.timeline_path)]
        summary = io.StringIO()
        with redirect_stdout(summary):
            self.assertEqual(main(args), 0)
        self.assertEqual(json.loads(summary.getvalue())["turn_count"], 81)
        self.assertNotIn(self.raw["segments"][0]["text"], summary.getvalue())
        output = Path(self.tmp.name) / "prepared.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(args + ["--output", str(output)]), 0)
        self.assertFalse(json.loads(output.read_text(encoding="utf-8"))["f5_ready"])
        before = self.transcript_path.read_bytes()
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(args + ["--output", str(self.transcript_path)])
        self.assertEqual(caught.exception.code, 1)
        self.assertEqual(self.transcript_path.read_bytes(), before)

    def test_malformed_file_has_safe_error(self):
        self.load()
        self.timeline_path.write_text("{bad-private-input", encoding="utf-8")
        with self.assertRaisesRegex(PreparationError, "^FIXTURE_UNREADABLE$"):
            load_fixture(self.transcript_path, self.timeline_path)


if __name__ == "__main__":
    unittest.main()
