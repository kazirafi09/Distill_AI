"""Isolated F11 identities with structured, versioned diagnostic payloads."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class WindTunnelRun(Base):
    __tablename__ = "wind_tunnel_runs"
    __table_args__ = (
        UniqueConstraint("job_id", "request_key", name="uq_wind_tunnel_request"),
        CheckConstraint("trigger IN ('approval', 'more')", name="ck_wind_tunnel_trigger"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'completed_with_issues', 'failed', 'deleting', 'cleanup_failed')", name="ck_wind_tunnel_status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="RESTRICT"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[str] = mapped_column(String(64))
    analysis_version: Mapped[str] = mapped_column(String(60))
    trigger: Mapped[str] = mapped_column(String(20))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    versions: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    findings: Mapped[list] = mapped_column(JSONB, default=list)
    resources: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WindTunnelDocument(Base):
    __tablename__ = "wind_tunnel_documents"
    __table_args__ = (
        UniqueConstraint("run_id", "persona_id", name="uq_wind_tunnel_persona"),
        UniqueConstraint("run_id", "id", name="uq_wind_tunnel_document_scope"),
        CheckConstraint("suffix IN ('.pdf', '.docx')", name="ck_wind_tunnel_document_type"),
        CheckConstraint("size_bytes > 0 AND size_bytes <= 10485760", name="ck_wind_tunnel_document_size"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("wind_tunnel_runs.id", ondelete="CASCADE"), index=True)
    persona_id: Mapped[str] = mapped_column(String(60))
    suffix: Mapped[str] = mapped_column(String(5))
    file_sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WindTunnelAssessment(Base):
    __tablename__ = "wind_tunnel_assessments"
    __table_args__ = (
        ForeignKeyConstraint(["run_id", "document_id"], ["wind_tunnel_documents.run_id", "wind_tunnel_documents.id"], ondelete="CASCADE"),
        UniqueConstraint("document_id", "phase", "repetition", name="uq_wind_tunnel_replicate"),
        CheckConstraint("phase IN ('baseline', 'proposed') AND repetition BETWEEN 1 AND 3", name="ck_wind_tunnel_replicate"),
        CheckConstraint("revision >= 0", name="ck_wind_tunnel_revision"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("wind_tunnel_runs.id", ondelete="CASCADE"), index=True)
    document_id: Mapped[UUID] = mapped_column(Uuid)
    phase: Mapped[str] = mapped_column(String(20), default="baseline")
    repetition: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class WindTunnelSuggestion(Base):
    __tablename__ = "wind_tunnel_suggestions"
    __table_args__ = (UniqueConstraint("run_id", "proposal_key", name="uq_wind_tunnel_proposal"),
                      CheckConstraint("status IN ('ready', 'applied', 'stale')", name="ck_wind_tunnel_suggestion_status"))
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("wind_tunnel_runs.id", ondelete="CASCADE"), index=True)
    proposal_key: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WindTunnelTask(Base):
    """Approval outbox, independent of model availability and configuration."""
    __tablename__ = "wind_tunnel_tasks"
    __table_args__ = (
        UniqueConstraint("job_id", "request_key", name="uq_wind_tunnel_task_request"),
        CheckConstraint("trigger IN ('approval', 'more')", name="ck_wind_tunnel_task_trigger"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'limited', 'failed', 'cancelled', 'stale')", name="ck_wind_tunnel_task_status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), index=True)
    job_id: Mapped[UUID] = mapped_column(ForeignKey("job_descriptions.id", ondelete="RESTRICT"), index=True)
    request_key: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("wind_tunnel_runs.id", ondelete="SET NULL"))
    claim: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
