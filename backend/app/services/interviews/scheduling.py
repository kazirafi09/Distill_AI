"""Invitation schedules reuse the existing interview session timestamps."""
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from app.models.interviews import InterviewSession

JOIN_EARLY_MINUTES = 10


class InvitationSchedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    application_id: UUID
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    display_timezone: str = Field(default="Asia/Dhaka", max_length=100)

    @model_validator(mode="after")
    def check_times(self):
        if self.ends_at <= self.starts_at:
            raise ValueError("Interview end time must be after the start time.")
        try:
            ZoneInfo(self.display_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Choose a valid interview timezone.") from None
        return self

    def public(self):
        return {"starts_at": self.starts_at.astimezone(timezone.utc).isoformat(),
                "ends_at": self.ends_at.astimezone(timezone.utc).isoformat(),
                "display_timezone": self.display_timezone}


async def application_session(db, application_id):
    return await db.scalar(select(InterviewSession).where(InterviewSession.application_id == application_id))


def schedule_for_session(session):
    if session is None or session.starts_at is None or session.ends_at is None:
        return None
    return InvitationSchedule(application_id=session.application_id, starts_at=session.starts_at,
        ends_at=session.ends_at, display_timezone=session.display_timezone or "UTC").public()


def join_window(session, *, now=None):
    schedule = schedule_for_session(session)
    if schedule is None:
        return None  # Existing unscheduled invitations retain their original behavior.
    now = now or datetime.now(timezone.utc)
    opens = session.starts_at - timedelta(minutes=JOIN_EARLY_MINUTES)
    return {**schedule, "join_opens_at": opens.isoformat(),
            "join_window_open": opens <= now < session.ends_at}


def require_join_window(session):
    window = join_window(session)
    if window and not window["join_window_open"]:
        raise HTTPException(409, "Joining is available from 10 minutes before the scheduled start until the scheduled end.")


def schedule_label(schedule):
    zone = ZoneInfo(schedule["display_timezone"])
    start = datetime.fromisoformat(schedule["starts_at"]).astimezone(zone)
    end = datetime.fromisoformat(schedule["ends_at"]).astimezone(zone)
    return f"{start:%A, %d %B %Y, %H:%M} – {end:%d %B %Y, %H:%M} ({zone.key})"
