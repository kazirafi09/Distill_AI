"""Request contracts for interviewee selections and scheduling."""

from datetime import timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, Field, model_validator

from app.services.interviews.shortlisting import ShortlistRequest


class SaveShortlistRequest(BaseModel):
    filters: ShortlistRequest
    preview_key: str = Field(pattern=r"^[0-9a-f]{64}$")
    selected_application_ids: list[UUID]

    @model_validator(mode="after")
    def validate_selected_applications(self):
        application_ids = self.selected_application_ids

        if len(application_ids) != len(set(application_ids)):
            raise ValueError("Selected applications must not contain duplicates.")

        return self


class ScheduleInterviewRequest(BaseModel):
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    display_timezone: str = Field(default="Asia/Dhaka", max_length=100)

    @model_validator(mode="after")
    def validate_schedule(self):
        try:
            ZoneInfo(self.display_timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Use a valid timezone, such as Asia/Dhaka.") from None

        self.starts_at = self.starts_at.astimezone(timezone.utc)
        self.ends_at = self.ends_at.astimezone(timezone.utc)
        if self.ends_at <= self.starts_at:
            raise ValueError("Interview end must be after its start.")
        return self
