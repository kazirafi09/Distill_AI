"""Owned assessment runs, durable stage checkpoints and immutable result payloads."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssessmentRun(Base):
    __tablename__ = "assessment_runs"
    __mapper_args__ = {"eager_defaults": True}
    __table_args__ = (
        UniqueConstraint("recruiter_id", "input_key", name="uq_assessment_run_input"),
        CheckConstraint("kind IN ('f5', 'f6', 'cv_claim_review', 'interview_performance', 'interview_quality')", name="ck_assessment_run_kind"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'needs_review', 'failed')", name="ck_assessment_run_status"),
        CheckConstraint("attempt >= 0 AND attempt <= 6", name="ck_assessment_run_attempt"),
        CheckConstraint("NOT synthetic OR (application_id IS NULL AND transcript_id IS NULL)", name="ck_assessment_synthetic_scope"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[UUID | None] = mapped_column(ForeignKey("candidate_applications.id", ondelete="CASCADE"))
    transcript_id: Mapped[UUID | None] = mapped_column(ForeignKey("interview_transcripts.id", ondelete="CASCADE"))
    # F6 pins the exact completed F5 run and CV assessment.
    parent_run_id: Mapped[UUID | None] = mapped_column(ForeignKey("assessment_runs.id", ondelete="CASCADE"))
    cv_assessment_id: Mapped[UUID | None] = mapped_column(ForeignKey("cv_assessments.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32), default="f5")
    synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    label: Mapped[str] = mapped_column(String(255))
    input_key: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(100), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    total_groups: Mapped[int] = mapped_column(Integer, default=0)
    completed_groups: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FinalCandidateSelection(Base):
    """Recruiter-confirmed hiring selection and the reviewed score snapshots."""
    __tablename__ = "final_candidate_selections"
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), primary_key=True)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    candidates: Mapped[list] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssessmentStage(Base):
    __tablename__ = "assessment_stages"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("assessment_runs.id", ondelete="CASCADE"), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssessmentAnswer(Base):
    __tablename__ = "assessment_answers"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("assessment_runs.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSONB)


class AssessmentResult(Base):
    __tablename__ = "assessment_results"
    run_id: Mapped[UUID] = mapped_column(ForeignKey("assessment_runs.id", ondelete="CASCADE"), primary_key=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InterviewScoringRevision(Base):
    __tablename__ = "interview_scoring_revisions"
    __table_args__ = (
        UniqueConstraint("job_id", "revision", name="uq_interview_scoring_revision"),
        CheckConstraint("revision > 0", name="ck_interview_scoring_revision_positive"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    policy: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssessmentFindingReview(Base):
    __tablename__ = "assessment_finding_reviews"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("assessment_runs.id", ondelete="CASCADE"), index=True)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    finding_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
