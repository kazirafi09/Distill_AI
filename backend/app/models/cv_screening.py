"""Authoritative, versioned screening inputs and durable processing records."""
from datetime import datetime
from uuid import UUID, uuid4
from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base


class CVIndex(Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "cv_indexes"
    __table_args__ = (UniqueConstraint("parse_result_id", "processing_key", name="uq_cv_index_processing"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    parse_result_id: Mapped[UUID] = mapped_column(ForeignKey("cv_parse_results.id", ondelete="CASCADE"), index=True)
    processing_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="pending")
    versions: Mapped[dict] = mapped_column(JSONB)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class ScreeningRun(Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "cv_screening_runs"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="CASCADE"), index=True)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    input_key: Mapped[str] = mapped_column(String(64))
    inputs: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class CVAssessment(Base):
    __mapper_args__ = {"eager_defaults": True}
    __tablename__ = "cv_assessments"
    __table_args__ = (UniqueConstraint("run_id", "application_id", name="uq_cv_assessment_run_application"),)
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("cv_screening_runs.id", ondelete="CASCADE"), index=True)
    application_id: Mapped[UUID] = mapped_column(ForeignKey("candidate_applications.id", ondelete="CASCADE"), index=True)
    file_version_ids: Mapped[list] = mapped_column(JSONB)
    parse_result_ids: Mapped[list] = mapped_column(JSONB, default=list)
    index_ids: Mapped[list] = mapped_column(JSONB, default=list)
    processing_key: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    matches: Mapped[list] = mapped_column(JSONB, default=list)
    score: Mapped[dict | None] = mapped_column(JSONB)
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    reused_from: Mapped[str | None] = mapped_column(String(36))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
