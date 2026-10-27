"""Validate source segments and derive aligned turns without changing the source.

Offsets must be provided explicitly. Database save times and independent provider
clocks cannot supply a missing offset. Live capture does not yet record the
continuous audio-to-session timing required by this module.
"""

import hashlib
import json
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator


MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_SEGMENTS = 20000
MAX_TIME_MS = 2147483647


class PreparationError(ValueError):
    """A safe error code, without transcript text or validation input."""


class SourceSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    id: UUID
    transcript_id: UUID
    stream_id: UUID
    source_event_key: str = Field(strict=True, min_length=1, max_length=160)
    speaker_role: Literal["recruiter", "candidate"]
    participant_identity: str = Field(strict=True, min_length=1, max_length=255)
    text: str = Field(strict=True, min_length=1, max_length=20000)
    stream_start_ms: int = Field(strict=True, ge=0, le=MAX_TIME_MS)
    stream_end_ms: int = Field(strict=True, ge=0, le=MAX_TIME_MS)
    session_start_ms: int | None = Field(default=None, strict=True, ge=0, le=MAX_TIME_MS)
    session_end_ms: int | None = Field(default=None, strict=True, ge=0, le=MAX_TIME_MS)
    quality_flags: list[str] = Field(max_length=100)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def valid_segment(self):
        if not self.text.strip() or self.stream_end_ms < self.stream_start_ms:
            raise ValueError("INVALID_SEGMENT")
        if (self.session_start_ms is None) != (self.session_end_ms is None):
            raise ValueError("INCOMPLETE_SESSION_TIMES")
        if self.session_start_ms is not None and self.session_end_ms < self.session_start_ms:
            raise ValueError("INVALID_SESSION_TIMES")
        expected = f"{self.stream_id}:{self.stream_start_ms}:{self.stream_end_ms}"
        if self.source_event_key != expected:
            raise ValueError("SOURCE_EVENT_KEY_MISMATCH")
        return self


class StreamOffset(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    stream_id: UUID
    speaker_role: Literal["recruiter", "candidate"]
    stream_origin_ms: int = Field(strict=True, ge=0, le=MAX_TIME_MS)
    origin_source: Literal["synthetic_authoring_ground_truth"]


class AlignedTurn(SourceSegment):
    session_start_ms: int = Field(strict=True, ge=0, le=MAX_TIME_MS)
    session_end_ms: int = Field(strict=True, ge=0, le=MAX_TIME_MS)
    timing_basis: Literal["synthetic_authoring_ground_truth", "finalized_session_timeline"]


class SavedTextTurn(SourceSegment):
    """Final saved speech with no asserted common-clock or verified chronology."""
    timing_basis: Literal["saved_text_order"] = "saved_text_order"

    @model_validator(mode="after")
    def no_invented_timing(self):
        if self.session_start_ms is not None or self.session_end_ms is not None:
            raise ValueError("TEXT_ONLY_TIMING_MUST_BE_UNKNOWN")
        return self


def ordered_turns(turns):
    if turns and turns[0].get("timing_basis") == "saved_text_order":
        return sorted(turns, key=lambda turn: (str(turn["created_at"]), turn["id"]))
    return sorted(turns, key=lambda turn: (turn["session_start_ms"], turn["session_end_ms"], turn["id"]))


def saved_text_content(transcript, rows, session):
    """Keep real text and raw clocks; do not derive common timing from save order."""
    result = []
    for row in rows:
        expected = (f"recruiter-{session.recruiter_id}" if row.speaker_role == "recruiter"
                    else f"candidate-{session.application_id}")
        if row.transcript_id != transcript.id or row.participant_identity != expected:
            raise ValueError("TRANSCRIPT_SOURCE_IDENTITY_INVALID")
        data = {name: getattr(row, name) for name in (
            "id", "transcript_id", "stream_id", "source_event_key", "speaker_role", "participant_identity", "text",
            "stream_start_ms", "stream_end_ms", "quality_flags", "created_at")}
        data.update(session_start_ms=None, session_end_ms=None, timing_basis="saved_text_order")
        result.append(SavedTextTurn.model_validate(data).model_dump(mode="json"))
    return ordered_turns(result)


def _parse(model, value):
    try:
        return model.model_validate(value)
    except (ValidationError, TypeError, ValueError):
        raise PreparationError("TRANSCRIPT_INPUT_INVALID") from None


def _digest(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def align_segments(transcript_id, segments, stream_offsets):
    """Return separate turns sorted by aligned start/end/ID; preserve overlaps.

    This implementation accepts synthetic, declared continuous stream clocks.
    A real capture adapter must supply measured audio-clock mapping before live
    offsets can be accepted. No grouping, correction, exclusion or scoring occurs.
    """
    try:
        transcript_id = UUID(str(transcript_id))
    except (TypeError, ValueError):
        raise PreparationError("TRANSCRIPT_INPUT_INVALID") from None
    if not isinstance(segments, list) or not 1 <= len(segments) <= MAX_SEGMENTS:
        raise PreparationError("TRANSCRIPT_SEGMENT_COUNT_INVALID")
    if not isinstance(stream_offsets, list) or not stream_offsets:
        raise PreparationError("STREAM_TIMING_MISSING")
    offsets = {}
    for item in stream_offsets:
        offset = _parse(StreamOffset, item)
        if offset.stream_id in offsets:
            raise PreparationError("DUPLICATE_STREAM_TIMING")
        offsets[offset.stream_id] = offset
    seen_ids, seen_events, identities, used_streams = set(), set(), {}, set()
    aligned = []
    for item in segments:
        segment = _parse(SourceSegment, item)
        if segment.transcript_id != transcript_id:
            raise PreparationError("TRANSCRIPT_SCOPE_MISMATCH")
        if segment.id in seen_ids or segment.source_event_key in seen_events:
            raise PreparationError("DUPLICATE_SOURCE_SEGMENT")
        seen_ids.add(segment.id)
        seen_events.add(segment.source_event_key)
        offset = offsets.get(segment.stream_id)
        if offset is None:
            raise PreparationError("STREAM_TIMING_MISSING")
        if offset.speaker_role != segment.speaker_role:
            raise PreparationError("TRANSCRIPT_SPEAKER_MISMATCH")
        identity = (segment.speaker_role, segment.participant_identity)
        if identities.setdefault(segment.stream_id, identity) != identity:
            raise PreparationError("TRANSCRIPT_SPEAKER_MISMATCH")
        used_streams.add(segment.stream_id)
        start = offset.stream_origin_ms + segment.stream_start_ms
        end = offset.stream_origin_ms + segment.stream_end_ms
        if end > MAX_TIME_MS:
            raise PreparationError("TRANSCRIPT_TIME_OVERFLOW")
        if segment.session_start_ms is not None and (
            segment.session_start_ms != start or segment.session_end_ms != end
        ):
            raise PreparationError("SESSION_TIMING_CONFLICT")
        data = segment.model_dump()
        data.update(session_start_ms=start, session_end_ms=end, timing_basis=offset.origin_source)
        aligned.append(AlignedTurn.model_validate(data))
    if used_streams != set(offsets):
        raise PreparationError("UNUSED_STREAM_TIMING")
    return sorted(aligned, key=lambda turn: (turn.session_start_ms, turn.session_end_ms, str(turn.id)))


def _read_json(path):
    try:
        with Path(path).open("rb") as handle:
            data = handle.read(MAX_JSON_BYTES + 1)
        if len(data) > MAX_JSON_BYTES:
            raise PreparationError("FIXTURE_TOO_LARGE")
        value = json.loads(data)
        if not isinstance(value, dict):
            raise PreparationError("FIXTURE_INVALID")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        raise PreparationError("FIXTURE_UNREADABLE") from None


def load_fixture(transcript_path, timeline_path):
    """Read only the raw fixture and stream offsets, never expected analysis.

    The timeline oracle also has turn-by-turn expected times and scenario labels.
    They are intentionally not used to derive the output, nor exposed in it.
    """
    raw, timeline = _read_json(transcript_path), _read_json(timeline_path)
    if (raw.get("fixture_schema") != "distill-interview-stored-segments-fixture-v1"
            or timeline.get("fixture_schema") != "distill-interview-timeline-oracle-v1"
            or raw.get("synthetic") is not True or timeline.get("synthetic") is not True):
        raise PreparationError("SYNTHETIC_FIXTURE_REQUIRED")
    fixture_id, revision = raw.get("fixture_id"), raw.get("fixture_revision")
    if (not isinstance(fixture_id, str) or not fixture_id or type(revision) is not int
            or revision < 1 or timeline.get("fixture_id") != fixture_id
            or type(timeline.get("fixture_revision")) is not int
            or timeline["fixture_revision"] != revision):
        raise PreparationError("FIXTURE_VERSION_MISMATCH")
    if (timeline.get("time_unit") != "milliseconds"
            or timeline.get("alignment_formula") != "session_ms = stream_origin_ms + stream_ms"):
        raise PreparationError("TIMELINE_CONTRACT_INVALID")
    source = raw.get("transcript")
    if (not isinstance(source, dict) or source.get("status") not in {
            "capturing", "finalizing", "completed", "needs_review", "failed"}
            or type(source.get("version")) is not int or source["version"] < 1
            or not isinstance(source.get("quality_flags"), list)
            or any(not isinstance(flag, str) for flag in source["quality_flags"])):
        raise PreparationError("FIXTURE_INVALID")
    turns = align_segments(source.get("id"), raw.get("segments"), timeline.get("streams"))
    input_snapshot = {
        "transcript": source,
        "segments": [turn.model_dump(mode="json") for turn in sorted(
            (_parse(SourceSegment, item) for item in raw["segments"]), key=lambda turn: str(turn.id))],
        "streams": sorted(timeline["streams"], key=lambda stream: stream["stream_id"]),
    }
    turn_data = [turn.model_dump(mode="json") for turn in turns]
    return {
        "schema": "distill-prepared-transcript-preview-v1",
        "fixture_id": fixture_id, "fixture_revision": revision,
        "synthetic": True, "status": "synthetic_preview", "f5_ready": False,
        "source_transcript_id": str(turns[0].transcript_id),
        "source_version": source["version"], "source_status": source["status"],
        "source_quality_flags": list(source["quality_flags"]),
        "preparation_flags": ["SYNTHETIC_TIMING_ONLY", "FINALIZATION_NOT_VERIFIED"],
        "source_snapshot_sha256": _digest(input_snapshot),
        "aligned_content_sha256": _digest(turn_data),
        "turns": turn_data,
    }
