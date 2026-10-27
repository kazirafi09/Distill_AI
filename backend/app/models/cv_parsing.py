from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CVParseResult(Base):
    __tablename__ = "cv_parse_results"
    __table_args__ = (
        UniqueConstraint("file_version_id", "processing_key", name="uq_cv_parse_version_processing"),
        CheckConstraint("status IN ('pending','verifying','extracting','ocr','text_saved','parsing','validating','completed','needs_review','failed')", name="ck_cv_parse_status"),
    )
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    file_version_id: Mapped[UUID] = mapped_column(ForeignKey("cv_file_versions.id", ondelete="CASCADE"), index=True)
    processing_key: Mapped[str] = mapped_column(String(64))
    file_sha256: Mapped[str] = mapped_column(String(64))
    versions: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String(24), default="pending")
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSONB, default=list)
    extraction_method: Mapped[str | None] = mapped_column(String(30))
    warnings: Mapped[list] = mapped_column(JSONB, default=list)
    profile: Mapped[dict | None] = mapped_column(JSONB)
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
