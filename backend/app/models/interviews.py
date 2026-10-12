"""Saved interviewee selections and their screening references."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
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


class InterviewInvitation(Base):
    """One current invitation per job application; raw tokens are never stored."""
    __tablename__ = "interview_invitations"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_description_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_applications.id", ondelete="CASCADE"), unique=True)
    recipient_email: Mapped[str] = mapped_column(String(320))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvitationBatch(Base):
    """A reviewed recipient/message snapshot, confirmed at most once."""
    __tablename__ = "interview_invitation_batches"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_description_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True)
    mode: Mapped[str] = mapped_column(String(24))
    preview_key: Mapped[str] = mapped_column(String(64))
    preview: Mapped[dict] = mapped_column(JSONB)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InvitationEmailJob(Base):
    """Durable individual send; encrypted payload contains the private link."""
    __tablename__ = "interview_invitation_email_jobs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    invitation_id: Mapped[UUID] = mapped_column(ForeignKey("interview_invitations.id", ondelete="CASCADE"), unique=True)
    batch_id: Mapped[UUID] = mapped_column(ForeignKey("interview_invitation_batches.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    encrypted_message: Mapped[str] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
