"""PCM sample clock checked against monotonic backend reception time.

This maps the audio received by the backend, not the participant's original
microphone hardware clock. No provider-result or database-arrival timestamps
are used. Delays/gaps exceeding the declared bound invalidate alignment.
"""
from dataclasses import dataclass, field

TIMING_BASIS = "backend_pcm_reception_monotonic_v1"
MAX_SKEW_MS = 250
SAMPLE_RATE = 16000


@dataclass
class AudioClock:
    session_elapsed_ms: float
    anchor_monotonic: float
    origin_ms: int | None = None
    samples_received: int = 0
    samples_sent: int = 0
    max_skew_ms: int = 0
    flags: set[str] = field(default_factory=set)
    _origin_monotonic: float | None = None

    def observe(self, samples: int, now: float):
        if type(samples) is not int or not 0 < samples <= SAMPLE_RATE:
            raise ValueError("AUDIO_FRAME_INVALID")
        duration = samples / SAMPLE_RATE
        if self._origin_monotonic is None:
            self._origin_monotonic = now - duration
            origin = self.session_elapsed_ms + (self._origin_monotonic - self.anchor_monotonic) * 1000
            if origin < 0:
                raise ValueError("AUDIO_CLOCK_INVALID")
            self.origin_ms = round(origin)
        self.samples_received += samples
        expected_end = self._origin_monotonic + self.samples_received / SAMPLE_RATE
        skew = round(abs(now - expected_end) * 1000)
        self.max_skew_ms = max(self.max_skew_ms, skew)
        if skew > MAX_SKEW_MS:
            self.flags.add("TIMING_UNCERTAIN")

    def sent(self, samples: int):
        self.samples_sent += samples

    def finish(self, now: float):
        if self._origin_monotonic is not None:
            skew = round(abs(now - self._origin_monotonic - self.samples_received / SAMPLE_RATE) * 1000)
            self.max_skew_ms = max(self.max_skew_ms, skew)
            if skew > MAX_SKEW_MS:
                self.flags.add("TIMING_UNCERTAIN")

    def summary(self):
        return {"origin_ms": self.origin_ms, "samples_sent": self.samples_sent,
                "max_skew_ms": self.max_skew_ms, "quality_flags": sorted(self.flags)}
