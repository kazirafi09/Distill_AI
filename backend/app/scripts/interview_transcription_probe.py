"""Run a bounded two-speaker Deepgram test against an active interview.

Audio is sent to Deepgram. Final transcript events are saved to PostgreSQL
and printed to the terminal; raw audio is not saved here. The transcript
remains capturing with stream-relative timestamps until finalization is added.
Run inside the backend container with at least one microphone available.
"""

import argparse
import asyncio
import json
import math
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from aiohttp import ClientResponseError, ClientTimeout
from livekit import api, rtc

from app.core.config import get_settings
from app.db.session import engine
from app.scripts.interview_audio_probe import active_session
from app.services.interviews.livekit import livekit_credentials
from app.services.interviews.transcription import transcribe_microphone
from app.services.interviews.transcripts import begin_transcript, mark_speech_boundary, save_final_turn


def error_code(error):
    """Return safe diagnostics without provider messages, URLs or credentials."""
    if isinstance(error, ClientResponseError):
        return {
            400: "DEEPGRAM_REQUEST_REJECTED",
            401: "DEEPGRAM_AUTH_FAILED",
            402: "DEEPGRAM_CREDITS_REQUIRED",
            403: "DEEPGRAM_ACCESS_DENIED",
            429: "DEEPGRAM_RATE_LIMITED",
        }.get(error.status, "PROVIDER_HTTP_FAILED")
    if isinstance(error, TimeoutError):
        return "PROBE_TIMEOUT"
    known = {
        "DEEPGRAM_API_KEY_MISSING", "INVALID_SPEAKER_ROLE",
        "INVALID_TEST_DURATION", "DEEPGRAM_SOCKET_FAILED",
        "DEEPGRAM_TRANSCRIPTION_FAILED", "TRANSCRIPTION_TEST_ENDED_EARLY",
        "LIVE_ROOM_MISSING", "MICROPHONE_REQUIRED",
        "ROOM_OR_TRACK_DISCONNECTED", "DUPLICATE_ROLE_MICROPHONE",
        "TRANSCRIPT_START_FAILED", "TRANSCRIPT_SAVE_FAILED", "TRANSCRIPT_SPEAKER_MISMATCH",
    }
    if isinstance(error, (ValueError, RuntimeError)) and str(error) in known:
        return str(error)
    return type(error).__name__


def format_timestamp(milliseconds):
    minutes, remainder = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def make_transcript_callback(
    transcript_id, recruiter_id, stream_id, participant_identity, role,
    display, counts,
):
    """Bind provider events to this trusted speaker and connection identity."""
    async def on_transcript(event):
        if event["role"] != role:
            raise RuntimeError("TRANSCRIPT_SPEAKER_MISMATCH")
        if event.get("kind") == "speech_boundary":
            try:
                await mark_speech_boundary(transcript_id, recruiter_id, stream_id, event["stream_end_ms"])
            except Exception:
                raise RuntimeError("TRANSCRIPT_SAVE_FAILED") from None
            return
        await display(event)
        if not event["is_final"]:
            return
        try:
            result = await save_final_turn(
                transcript_id=transcript_id,
                recruiter_id=recruiter_id,
                stream_id=stream_id,
                participant_identity=participant_identity,
                event=event,
            )
        except Exception:
            # Never put database/provider payloads or transcript text in errors.
            raise RuntimeError("TRANSCRIPT_SAVE_FAILED") from None
        if result["saved"]:
            counts["saved_turns"] += 1
        elif result.get("reason") == "duplicate":
            counts["duplicate_events"] += 1

    return on_transcript


def print_summary(summary, json_output=False):
    if json_output:
        print(json.dumps(summary), flush=True)
        return
    print("\n--- Transcription test summary ---", flush=True)
    print(f"JD: {summary['job_number']} | Duration: {summary['seconds_per_stream']} seconds", flush=True)
    for role, counts in summary["roles"].items():
        status = counts["status"].replace("_", " ")
        print(
            f"{role.title()}: {status} | {counts['final_events']} final, "
            f"{counts['interim_events']} interim | {counts['saved_turns']} saved, "
            f"{counts['duplicate_events']} duplicates skipped",
            flush=True,
        )
    if summary["failures"]:
        print("Errors: " + ", ".join(summary["failures"]), flush=True)
    result = "PASS" if summary["passed"] else "FAIL" if summary["failures"] else "NO FINAL SPEECH DETECTED"
    print(f"Result: {result}", flush=True)
    if summary["transcript_id"]:
        print(f"Transcript ID: {summary['transcript_id']} | Version: {summary['transcript_version']}", flush=True)
    print(f"Saved to PostgreSQL: {summary['saved_to_postgresql']}", flush=True)
    print("Stream-relative timestamps; transcript remains capturing. Alignment and finalization are pending.", flush=True)


async def probe(job_number, seconds, json_output=False, show_interim=False):
    settings = get_settings()
    if not settings.deepgram_api_key or not settings.deepgram_api_key.get_secret_value().strip():
        raise ValueError("DEEPGRAM_API_KEY_MISSING")

    # Reuse the existing read-only lookup; requires one active session for this JD.
    session = await active_session(job_number)
    key, secret = livekit_credentials(settings)
    expected = {
        f"recruiter-{session.recruiter_id}": "recruiter",
        f"candidate-{session.application_id}": "candidate",
    }
    async with api.LiveKitAPI(
        url=settings.livekit_api_url, api_key=key, api_secret=secret,
        timeout=ClientTimeout(total=10),
    ) as client:
        rooms = await client.room.list_rooms(api.ListRoomsRequest(names=[session.room_name]))
        if not rooms.rooms:
            raise RuntimeError("LIVE_ROOM_MISSING")

    token = (
        api.AccessToken(key, secret)
        .with_identity(f"transcription-probe-{uuid4()}")
        .with_ttl(timedelta(minutes=3))
        .with_grants(api.VideoGrants(
            room_join=True, room=session.room_name,
            can_publish=False, can_publish_data=False, can_subscribe=True,
            can_update_own_metadata=False,
        ))
        .to_jwt()
    )
    address = urlsplit(settings.livekit_api_url)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[address.scheme]
    rtc_url = urlunsplit((scheme, address.netloc, address.path, "", ""))
    room = rtc.Room()
    ready = asyncio.Event()
    interrupted = asyncio.Event()
    selected = {}
    failures = []
    tasks = []
    stream_ids = set()
    deadline = None
    transcript_info = None
    stopping = False
    totals = {role: {"interim_events": 0, "final_events": 0,
                     "saved_turns": 0, "duplicate_events": 0} for role in expected.values()}

    async def emit(event):
        kind = "final_events" if event["is_final"] else "interim_events"
        totals[event["role"]][kind] += 1
        if json_output:
            print(json.dumps({"type": "transcript", **event}, ensure_ascii=False), flush=True)
        elif event["is_final"] or show_interim:
            start = format_timestamp(event["stream_start_ms"])
            end = format_timestamp(event["stream_end_ms"])
            label = "" if event["is_final"] else " [interim]"
            text = " ".join(event["text"].split())
            print(f"[{start} - {end}] {event['role'].title()}{label}: {text}", flush=True)

    def stream_finished(task):
        if stopping or task.cancelled():
            return
        error = task.exception()
        if error is not None:
            failures.append(error_code(error))
            interrupted.set()

    def start_stream(role):
        if stopping or deadline is None:
            return
        publication, track = selected[role]
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0 or publication.sid in stream_ids:
            return
        stream_ids.add(publication.sid)
        # A newly published microphone can join during the test. The bridge's
        # minimum diagnostic duration is five seconds; allow its tail to drain.
        duration = max(5, min(seconds, math.ceil(remaining)))
        # One UUID per actual Deepgram connection; this closure retains it for
        # all events/repeated deliveries from that connection.
        callback = make_transcript_callback(
            transcript_id=UUID(transcript_info["transcript_id"]),
            recruiter_id=session.recruiter_id,
            stream_id=uuid4(),
            participant_identity=next(identity for identity, value in expected.items() if value == role),
            role=role,
            display=emit,
            counts=totals[role],
        )
        task = asyncio.create_task(transcribe_microphone(track, role, callback, duration))
        task.add_done_callback(stream_finished)
        tasks.append(task)

    def select_microphone(publication, participant):
        if (
            not stopping and participant.identity in expected
            and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
        ):
            publication.set_subscribed(True)

    room.on("track_published", select_microphone)

    @room.on("track_subscribed")
    def track_subscribed(track, publication, participant):
        role = expected.get(participant.identity)
        if (
            stopping or role is None
            or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
            or track.kind != rtc.TrackKind.KIND_AUDIO
        ):
            return
        if role in selected and selected[role][0].sid != publication.sid:
            failures.append("DUPLICATE_ROLE_MICROPHONE")
            interrupted.set()
            return
        selected[role] = (publication, track)
        ready.set()
        start_stream(role)

    @room.on("track_unsubscribed")
    def track_unsubscribed(track, publication, participant):
        if not stopping and any(publication.sid == pub.sid for pub, _ in selected.values()):
            interrupted.set()

    @room.on("disconnected")
    def disconnected(reason):
        if not stopping:
            interrupted.set()

    watcher = None
    combined = None
    ready_task = None
    observation = None
    try:
        await asyncio.wait_for(
            room.connect(rtc_url, token, options=rtc.RoomOptions(auto_subscribe=False)),
            timeout=15,
        )
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                select_microphone(publication, participant)

        print("Waiting up to 20 seconds for a microphone track. One participant may stay muted.", flush=True)
        ready_task = asyncio.create_task(ready.wait())
        watcher = asyncio.create_task(interrupted.wait())
        await asyncio.wait({ready_task, watcher}, timeout=20, return_when=asyncio.FIRST_COMPLETED)
        if interrupted.is_set():
            raise RuntimeError(failures[0] if failures else "ROOM_OR_TRACK_DISCONNECTED")
        if not ready.is_set():
            raise RuntimeError("MICROPHONE_REQUIRED")

        try:
            transcript_info = await begin_transcript(
                session.job_description_id, session.application_id, session.recruiter_id,
            )
        except Exception:
            raise RuntimeError("TRANSCRIPT_START_FAILED") from None
        if not json_output:
            print(
                f"Saving final segments to transcript {transcript_info['transcript_id']} "
                f"(version {transcript_info['version']}).",
                flush=True,
            )

        print(
            f"Starting {seconds}-second transcription streams; allow a few seconds for connection setup. "
            "Speak using either microphone; a muted or silent role does not fail the test. "
            "Keep the interview open until the summary appears.",
            flush=True,
        )
        deadline = asyncio.get_running_loop().time() + seconds
        for role in list(selected):
            start_stream(role)
        observation = asyncio.create_task(asyncio.sleep(seconds))
        finished, _ = await asyncio.wait(
            {observation, watcher}, timeout=seconds + 5,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if interrupted.is_set():
            raise RuntimeError(failures[0] if failures else "ROOM_OR_TRACK_DISCONNECTED")
        if observation not in finished:
            raise TimeoutError()
        # Include tracks published after the observation began, then allow
        # Deepgram CloseStream/final results to finish before disconnecting.
        combined = asyncio.gather(*tasks)
        await asyncio.wait_for(combined, timeout=30)
    except Exception as error:
        failures.append(error_code(error))
    finally:
        stopping = True
        pending = [*tasks, *([watcher] if watcher else []),
                   *([ready_task] if ready_task else []),
                   *([observation] if observation else [])]
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        if combined is not None:
            # Retrieve a cancellation/exception from the gather future as well.
            await asyncio.gather(combined, return_exceptions=True)
        try:
            await asyncio.wait_for(room.disconnect(), timeout=5)
        except Exception as error:
            failures.append(error_code(error))

    for role, counts in totals.items():
        if counts["final_events"]:
            counts["status"] = "transcribed"
        elif role not in selected:
            counts["status"] = "microphone_unavailable"
        elif selected[role][0].muted:
            counts["status"] = "muted"
        else:
            counts["status"] = "no_final_transcript"
    # A quiet or muted participant is not evidence of a broken transcription
    # stream. At least one role must still produce final text to pass.
    saved_to_postgresql = any(counts["saved_turns"] > 0 for counts in totals.values())
    final_received = any(counts["final_events"] > 0 for counts in totals.values())
    persisted = any(counts["saved_turns"] + counts["duplicate_events"] > 0 for counts in totals.values())
    passed = not failures and final_received and persisted
    print_summary({
        "type": "summary", "job_number": job_number, "seconds_per_stream": seconds,
        "roles": totals, "failures": list(dict.fromkeys(failures)), "passed": passed,
        "timestamps": "relative_to_each_stream", "saved_to_postgresql": saved_to_postgresql,
        "transcript_id": transcript_info["transcript_id"] if transcript_info else None,
        "transcript_version": transcript_info["version"] if transcript_info else None,
        "transcript_status": "capturing" if transcript_info else None,
    }, json_output=json_output)
    return passed


async def main(job_number, seconds, json_output=False, show_interim=False):
    try:
        return await probe(job_number, seconds, json_output, show_interim)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-number", type=int, required=True)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--json", action="store_true", help="Print raw transcript events and summary as JSON")
    parser.add_argument("--show-interim", action="store_true", help="Also print changing interim text (final text only by default)")
    args = parser.parse_args()
    if args.job_number < 1 or not 5 <= args.seconds <= 60:
        parser.error("Use a positive job number and a duration of 5-60 seconds")
    try:
        passed = asyncio.run(main(args.job_number, args.seconds, args.json, args.show_interim))
    except Exception as error:
        if args.json:
            print(json.dumps({"type": "summary", "passed": False, "error_code": error_code(error)}), flush=True)
        else:
            print(f"Transcription test failed: {error_code(error)}", flush=True)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    raise SystemExit(0 if passed else 1)
