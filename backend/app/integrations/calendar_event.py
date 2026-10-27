"""Private, recipient-specific calendar links and interoperable ICS attachments."""
from datetime import datetime, timezone
from urllib.parse import urlencode
from uuid import UUID

from pydantic import AwareDatetime, AnyHttpUrl, BaseModel, Field, model_validator


class CalendarEvent(BaseModel):
    uid: UUID
    sequence: int = Field(default=0, ge=0)
    title: str = Field(min_length=1, max_length=250)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    link: AnyHttpUrl = Field(repr=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("Calendar event end must follow its start.")
        return self


def utc_stamp(value):
    return value.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def google_calendar_link(title, schedule, link):
    return "https://calendar.google.com/calendar/render?" + urlencode({
        "action": "TEMPLATE", "text": title,
        "dates": f"{utc_stamp(datetime.fromisoformat(schedule['starts_at']))}/{utc_stamp(datetime.fromisoformat(schedule['ends_at']))}",
        "ctz": schedule["display_timezone"], "location": link,
        "details": f"Join your private interview: {link}\nPlease do not share this link. Recruiter admission is required.",
    })


def ics_text(value):
    return str(value).replace("\\", "\\\\").replace("\r\n", "\n").replace("\r", "\n").replace("\n", "\\n").replace(";", "\\;").replace(",", "\\,")


def calendar_ics(event):
    # Fold at 75 UTF-8 octets without splitting a Unicode character (RFC 5545).
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Distill AI//Interviews//EN", "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH", "BEGIN:VEVENT", f"UID:interview-{event.uid}@distill.ai", f"SEQUENCE:{event.sequence}",
        f"DTSTAMP:{utc_stamp(datetime.now(timezone.utc))}", f"DTSTART:{utc_stamp(event.starts_at)}",
        f"DTEND:{utc_stamp(event.ends_at)}", f"SUMMARY:{ics_text(event.title)}",
        f"DESCRIPTION:{ics_text('Join your private interview: ' + str(event.link))}",
        f"LOCATION:{ics_text(event.link)}", f"URL:{event.link}", "STATUS:CONFIRMED", "END:VEVENT", "END:VCALENDAR"]
    folded = []
    for line in lines:
        part = ""
        for char in line:
            if len((part + char).encode("utf-8")) > 75:
                folded.append(part)
                part = " "
            part += char
        folded.append(part)
    return "\r\n".join(folded) + "\r\n"
