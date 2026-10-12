"""Request contracts for saved interviewee selections."""

from uuid import UUID

from pydantic import BaseModel, Field, model_validator

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
