"""Saved interviewee selections and their screening references."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class InterviewShortlist(Base):
    __tablename__ = "interview_shortlists"

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    job_description_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"),
        unique=True,
    )
    recruiter_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    screening_run_id: Mapped[UUID] = mapped_column(
        ForeignKey("cv_screening_runs.id", ondelete="CASCADE")
    )

    # Keep the filter and original proposal for selection provenance.
    filter_inputs: Mapped[dict] = mapped_column(JSONB)
    proposed_application_ids: Mapped[list] = mapped_column(JSONB)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )


class InterviewShortlistMember(Base):
    __tablename__ = "interview_shortlist_members"
    __table_args__ = (
        UniqueConstraint(
            "shortlist_id",
            "application_id",
            name="uq_interview_shortlist_application",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid4
    )
    shortlist_id: Mapped[UUID] = mapped_column(
        ForeignKey("interview_shortlists.id", ondelete="CASCADE"),
        index=True,
    )
    application_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidate_applications.id", ondelete="CASCADE"),
        index=True,
    )
    assessment_id: Mapped[UUID] = mapped_column(
        ForeignKey("cv_assessments.id", ondelete="CASCADE")
    )

    # Preserve the score and rank used when the selection was saved.
    score_snapshot: Mapped[dict] = mapped_column(JSONB)
    rank_snapshot: Mapped[int] = mapped_column(Integer)
    manually_added: Mapped[bool] = mapped_column(Boolean, default=False)
