"""Minimal import exclusions and retryable external-storage cleanup."""
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CVImportExclusion(Base):
    __tablename__ = "cv_import_exclusions"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("cv_sources.id", ondelete="CASCADE"), primary_key=True)
    file_key: Mapped[str] = mapped_column(String(64), primary_key=True)


class CandidateDeletion(Base):
    __tablename__ = "candidate_deletions"
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    recruiter_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    application_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    manifest: Mapped[dict] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
