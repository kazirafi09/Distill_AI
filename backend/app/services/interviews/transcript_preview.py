"""Ephemeral speech hypotheses for display only; never assessment evidence."""
from dataclasses import dataclass, field


@dataclass
class TranscriptPreview:
    drafts: dict = field(default_factory=dict)

    def update(self, stream_id, event):
        if event.get("is_final"):
            return
        key = str(stream_id)
        text = event.get("text", "").strip()
        if not text:
            self.discard(stream_id)
            return
        start, end = event.get("stream_start_ms"), event.get("stream_end_ms")
        if (event.get("role") not in {"recruiter", "candidate"} or type(start) is not int
                or type(end) is not int or start < 0 or end < start):
            return
        # One replaceable draft per connection, not an ever-growing list of words.
        if key not in self.drafts and len(self.drafts) >= 4:
            self.drafts.pop(next(iter(self.drafts)))
        self.drafts[key] = {"stream_id": key, "speaker_role": event["role"],
            "text": text[:20000], "stream_start_ms": start, "stream_end_ms": end}

    def discard(self, stream_id):
        self.drafts.pop(str(stream_id), None)

    def snapshot(self):
        return [dict(draft) for draft in self.drafts.values()]
