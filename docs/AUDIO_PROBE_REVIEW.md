# Backend audio-access probe — code review

13 October 2026 (Asia/Dhaka). User authorized implementation and execution. Added the reviewed script and livekit==1.1.20 requirement, installed the media SDK in the existing backend container for testing, and ran the 20-second JD 180 probe. Both roles received 1,961 frames / 19.61 seconds, with nonzero levels, no failures and passed=true. Audio was processed transiently; no recording or Deepgram request. Review text below preserves the approved design; details are in DAILY_WORK_2026-10-13.md.

## Understanding and scope

The live JD 180 room has separate recruiter and candidate microphone publications. Test whether the backend can actually receive each source as PCM audio, keeping its role separate. This is a diagnostic for the future post-interview recording/Deepgram workflow, not a transcription service.

Add one dependency and one command-line script. The probe reads the selected in-progress session, confirms its live room exists, signs a unique subscribe-only participant token, subscribes only to the exact expected identities' microphone tracks and counts frames/sample energy. Raw frame buffers are processed transiently and not written, logged, transcribed or transmitted to another service. It disconnects after a bounded duration and attempts bounded stream/room cleanup.

Run with both test participants aware of the diagnostic and speaking in turn. Positive frame counts prove media reception. Nonzero levels can be speech, noise or echo; they do not prove transcript accuracy or intelligibility. Silence while muted is not a media failure. This is intended for the current LiveKit Cloud endpoint; the local container's advertised loopback media IP is unsuitable for a subscriber in a separate backend container.

## 1. Addition to backend/requirements.txt

```text
livekit==1.1.20
```

Keep livekit-api==1.2.1. The extra package supplies rtc.Room and AudioStream; no Deepgram package/key is needed.

## 2. New backend/app/scripts/interview_audio_probe.py

```python
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
```

## What each part does

- active_session() reads exactly one in-progress session and uses its existing room/role identifiers. It fails on an ambiguous JD rather than subscribe to a guessed room. No database writes.
- Subscribe-only token cannot publish audio/video/data or manage rooms. Automatic subscription is off; only expected microphone publications are selected, including publications arriving after connection. Camera and unrelated participant tracks are not requested.
- Each track reader aggregates 16 kHz mono signed PCM samples under its backend-owned role. A replaced track can create another reader/track count. This diagnostic aggregates by role; it does not implement recording segments or durable time alignment.
- Bounded queues avoid retaining a large audio backlog. Reader failures and premature disconnect cause a failed result. The observation period is 20 seconds by default, plus bounded connection and cleanup time. No transcript, sample data or token is printed.
- The probe is a visible temporary third participant, using a unique identity so it does not displace either user. It can briefly show a camera-off tile in the minimal UI.

Example summary (illustrative only):

```json
{
  "roles": {
    "recruiter": {"frames": 900, "audio_received": true},
    "candidate": {"frames": 850, "audio_received": true}
  },
  "passed": true
}
```

No production API, schema or Deepgram integration is changed. A successful probe is evidence for separate source reception, not persisted recording or end-to-end transcription. This is a manually run diagnostic; production access checks/consent/lifecycle belong in the future recording worker.

After approval, user runs Docker dependency rebuild and the command:

```powershell
docker compose build backend
docker compose up -d --no-deps backend
docker compose exec -T backend python -m app.scripts.interview_audio_probe --job-number 180 --seconds 20
```

Rejoin both participants after backend recreation if needed and enable microphones before the probe. User controls execution; no probe has been run during review.

References: [media SDK release](https://pypi.org/project/livekit/), [AudioStream](https://docs.livekit.io/reference/python/livekit/rtc/audio_stream.html), [Room](https://docs.livekit.io/reference/python/livekit/rtc/room.html), [track publications](https://docs.livekit.io/reference/python/livekit/rtc/track_publication.html), [audio frames](https://docs.livekit.io/reference/python/livekit/rtc/audio_frame.html).
