"""Bounded LiveKit-to-Deepgram bridge for a reviewed transcription diagnostic.

The caller supplies an authorized microphone track and its trusted speaker role.
This module does not join rooms, save transcripts, or publish events to the UI.
Timestamps are relative to this Deepgram stream, not the interview timeline.
"""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlencode

from aiohttp import ClientSession, ClientTimeout, WSMsgType
from livekit import rtc

from app.core.config import get_settings

logger = logging.getLogger("distill_ai.transcription")


def flush_confirmed(close_code, final_metadata):
    # Deepgram sends summary Metadata after CloseStream and before closing.
    # Some client close paths do not expose a close code after iteration ends.
    return close_code == 1000 or (final_metadata and close_code in {None, 1001})


async def transcribe_microphone(
    track,
    role: str,
    emit: Callable[[dict], Awaitable[None]],
    seconds: int | None = 60,
    *,
    stop_event: asyncio.Event | None = None,
    ready_event: asyncio.Event | None = None,
    audio_clock=None,
    on_ready=None,
):
    """Transcribe until a diagnostic timeout or the lifecycle stop signal."""

    if role not in {"recruiter", "candidate"}:
        raise ValueError("INVALID_SPEAKER_ROLE")

    if (seconds is None and stop_event is None) or (seconds is not None and not 5 <= seconds <= 60):
        raise ValueError("INVALID_TEST_DURATION")

    settings = get_settings()
    api_key = settings.deepgram_api_key

    if not api_key or not api_key.get_secret_value().strip():
        raise ValueError("DEEPGRAM_API_KEY_MISSING")

    parameters = {
        "model": settings.deepgram_model,
        "language": settings.deepgram_language,
        "encoding": "linear16",
        "sample_rate": 16000,
        "channels": 1,
        "interim_results": "true",
        "punctuate": "true",
        "smart_format": "true",
        "endpointing": settings.deepgram_endpointing_ms,
    }
    if settings.deepgram_model.startswith("nova-3") and settings.deepgram_keyterms:
        parameters["keyterm"] = settings.deepgram_keyterms

    url = "wss://api.deepgram.com/v1/listen?" + urlencode(parameters, doseq=True)
    headers = {
        "Authorization": f"Token {api_key.get_secret_value()}",
    }

    timeout = ClientTimeout(total=None, sock_connect=10)

    async with ClientSession(timeout=timeout) as client:
        socket = await asyncio.wait_for(
            client.ws_connect(url, headers=headers, heartbeat=20), timeout=15,
        )
        async with socket:
            audio = rtc.AudioStream.from_track(
                track=track,
                sample_rate=16000,
                num_channels=1,
                # The SDK's bounded RingQueue silently discards old frames.
                # Read independently of the network and detect our own overflow.
                capacity=0,
            )

            queue = asyncio.Queue(maxsize=250)
            closing_requested = False
            final_metadata = False

            async def read_audio():
                async for event in audio:
                    frame = event.frame
                    if frame.sample_rate != 16000 or frame.num_channels != 1:
                        raise ValueError("AUDIO_FRAME_INVALID")
                    if audio_clock is not None:
                        audio_clock.observe(frame.samples_per_channel, asyncio.get_running_loop().time())
                    try:
                        queue.put_nowait((frame.data.tobytes(), frame.samples_per_channel))
                    except asyncio.QueueFull:
                        raise ValueError("AUDIO_BUFFER_OVERFLOW") from None

            async def send_audio():
                while True:
                    item = await queue.get()
                    if item is None:
                        return
                    data, samples = item
                    await socket.send_bytes(data)
                    if audio_clock is not None:
                        audio_clock.sent(samples)

            async def keep_alive():
                # Deepgram application-level keepalive.
                while True:
                    await asyncio.sleep(4)
                    await socket.send_json({"type": "KeepAlive"})

            async def receive_results():
                nonlocal final_metadata
                async for message in socket:
                    if message.type == WSMsgType.ERROR:
                        raise RuntimeError("DEEPGRAM_SOCKET_FAILED")

                    if message.type != WSMsgType.TEXT:
                        continue

                    result = json.loads(message.data)

                    if result.get("type") == "Error":
                        raise RuntimeError("DEEPGRAM_TRANSCRIPTION_FAILED")

                    if result.get("type") == "Metadata" and closing_requested:
                        final_metadata = True

                    if result.get("type") != "Results":
                        continue

                    alternatives = result["channel"]["alternatives"]
                    if not alternatives:
                        continue

                    text = alternatives[0].get("transcript", "").strip()
                    boundary = result.get("speech_final")
                    if not text and not boundary and result.get("is_final", False):
                        continue

                    await emit({
                        "role": role,
                        "text": text,
                        "is_final": result.get("is_final", False),
                        "stream_start_ms": round(result["start"] * 1000),
                        "stream_end_ms": round(
                            (result["start"] + result["duration"]) * 1000
                        ),
                        "words": alternatives[0].get("words", []),
                        "speech_final": boundary,
                        "from_finalize": result.get("from_finalize", False),
                        "kind": "segment" if text else "speech_boundary",
                    })

            reader = asyncio.create_task(read_audio())
            sender = asyncio.create_task(send_audio())
            receiver = asyncio.create_task(receive_results())
            keeper = asyncio.create_task(keep_alive())
            tasks = {reader, sender, receiver, keeper}
            stopper = asyncio.create_task(stop_event.wait()) if stop_event is not None else None
            if stopper is not None:
                tasks.add(stopper)
            if ready_event is not None:
                ready_event.set()
            try:
                if on_ready is not None:
                    await on_ready()
                finished, _ = await asyncio.wait(
                    tasks,
                    timeout=seconds,
                    return_when=asyncio.FIRST_COMPLETED,
                )

                if finished:
                    # Surface an actual task failure before reporting early exit.
                    for task in finished:
                        task.result()
                    if stop_event is None or not stop_event.is_set():
                        raise ValueError("TRANSCRIPTION_CONNECTION_CLOSED")

                # Stop capture first, drain already received PCM, then close the
                # provider stream. Cancelling the sender loses queued tail audio.
                reader.cancel()
                keeper.cancel()
                await asyncio.gather(
                    reader,
                    keeper,
                    return_exceptions=True,
                )
                if audio_clock is not None:
                    audio_clock.finish(asyncio.get_running_loop().time())
                await asyncio.wait_for(queue.put(None), timeout=3)
                await asyncio.wait_for(sender, timeout=5)
                closing_requested = True
                await socket.send_json({"type": "CloseStream"})
                await asyncio.wait_for(receiver, timeout=10)
                if not flush_confirmed(socket.close_code, final_metadata):
                    logger.warning("Transcription flush unconfirmed; role=%s; close_code=%s; final_metadata=%s",
                                   role, socket.close_code, final_metadata)
                    raise ValueError("TRANSCRIPTION_FLUSH_FAILED")

            finally:
                for task in tasks:
                    task.cancel()

                await asyncio.gather(*tasks, return_exceptions=True)
                await asyncio.wait_for(audio.aclose(), timeout=3)


async def print_transcript(event: dict):
    """Explicit diagnostic callback; prints transcript content to the terminal."""
    print(json.dumps(event, ensure_ascii=False), flush=True)
