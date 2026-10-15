"""Count microphone frames by role without saving or transcribing audio."""

import argparse
import asyncio
import json
import math
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

from aiohttp import ClientTimeout
from livekit import api, rtc
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import SessionFactory, engine
from app.models.interviews import InterviewSession
from app.models.jd import JobDescription
from app.services.interviews.livekit import livekit_credentials


async def active_session(job_number):
    async with SessionFactory() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        rows = (await db.execute(
            select(
                InterviewSession.room_name,
                InterviewSession.recruiter_id,
                InterviewSession.application_id,
                InterviewSession.job_description_id,
            )
            .join(JobDescription, JobDescription.id == InterviewSession.job_description_id)
            .where(JobDescription.number == job_number, InterviewSession.status == "in_progress")
        )).all()
        if len(rows) != 1:
            raise RuntimeError("Expected exactly one in-progress session for this JD")
        return rows[0]


async def collect_audio(track, statistics):
    stream = rtc.AudioStream.from_track(
        track=track, sample_rate=16000, num_channels=1, capacity=50,
    )
    try:
        async for event in stream:
            frame = event.frame
            samples = frame.data
            statistics["frames"] += 1
            statistics["samples"] += len(samples)
            statistics["energy"] += sum(int(value) ** 2 for value in samples)
            statistics["peak"] = max(
                statistics["peak"], max((abs(int(value)) for value in samples), default=0),
            )
    finally:
        await asyncio.wait_for(stream.aclose(), timeout=3)


async def probe(job_number, seconds):
    session = await active_session(job_number)
    settings = get_settings()
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
            raise RuntimeError("Live room is absent; rejoin before running the probe")

    token = (
        api.AccessToken(key, secret)
        .with_identity(f"audio-probe-{uuid4()}")
        .with_ttl(timedelta(minutes=2))
        .with_grants(api.VideoGrants(
            room_join=True, room=session.room_name,
            can_publish=False, can_publish_data=False, can_subscribe=True,
            can_update_own_metadata=False,
        ))
        .to_jwt()
    )
    # The backend address may differ from the browser address inside Docker.
    address = urlsplit(settings.livekit_api_url)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[address.scheme]
    rtc_url = urlunsplit((scheme, address.netloc, address.path, "", ""))
    room = rtc.Room()
    statistics = {
        role: {"frames": 0, "samples": 0, "energy": 0, "peak": 0, "tracks_seen": 0}
        for role in ("recruiter", "candidate")
    }
    readers = {}
    failures = []
    stopping = False
    disconnected = asyncio.Event()

    def select_microphone(publication, participant):
        if (
            not stopping
            and participant.identity in expected
            and publication.source == rtc.TrackSource.SOURCE_MICROPHONE
        ):
            publication.set_subscribed(True)

    @room.on("track_published")
    def track_published(publication, participant):
        select_microphone(publication, participant)

    @room.on("track_subscribed")
    def track_subscribed(track, publication, participant):
        role = expected.get(participant.identity)
        if (
            stopping or role is None
            or publication.source != rtc.TrackSource.SOURCE_MICROPHONE
            or track.kind != rtc.TrackKind.KIND_AUDIO
            or publication.sid in readers
        ):
            return
        statistics[role]["tracks_seen"] += 1
        readers[publication.sid] = asyncio.create_task(collect_audio(track, statistics[role]))

    @room.on("disconnected")
    def on_disconnected(reason):
        disconnected.set()

    try:
        await asyncio.wait_for(
            room.connect(rtc_url, token, options=rtc.RoomOptions(auto_subscribe=False)),
            timeout=15,
        )
        for participant in room.remote_participants.values():
            for publication in participant.track_publications.values():
                select_microphone(publication, participant)
        print("Audio probe started. Speak in turn for the next " + str(seconds) + " seconds.", flush=True)
        try:
            await asyncio.wait_for(disconnected.wait(), timeout=seconds)
            failures.append("room_disconnected_before_timeout")
        except TimeoutError:
            pass
    finally:
        stopping = True
        for task in readers.values():
            if not task.done():
                task.cancel()
        if readers:
            results = await asyncio.gather(*readers.values(), return_exceptions=True)
            for result in results:
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
                    failures.append(type(result).__name__)
        await asyncio.wait_for(room.disconnect(), timeout=5)

    report = {}
    for role, totals in statistics.items():
        report[role] = {
            "tracks_seen": totals["tracks_seen"],
            "frames": totals["frames"],
            "received_audio_seconds": round(totals["samples"] / 16000, 2),
            "peak_pcm": totals["peak"],
            "rms_pcm": round(math.sqrt(totals["energy"] / max(totals["samples"], 1)), 2),
            "audio_received": totals["frames"] > 0,
        }
    passed = all(result["audio_received"] for result in report.values()) and not failures
    print(json.dumps({"job_number": job_number, "roles": report, "failures": failures, "passed": passed}))
    return passed


async def main(job_number, seconds):
    try:
        return await probe(job_number, seconds)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-number", required=True, type=int)
    parser.add_argument("--seconds", default=20, type=int)
    args = parser.parse_args()
    if args.job_number < 1 or not 5 <= args.seconds <= 60:
        parser.error("Use a positive job number and a duration of 5–60 seconds")
    try:
        success = asyncio.run(main(args.job_number, args.seconds))
    except Exception as error:
        # Do not print SDK exception payloads containing URLs/credentials.
        print(json.dumps({"passed": False, "error_type": type(error).__name__}))
        raise SystemExit(1) from None
    raise SystemExit(0 if success else 1)
