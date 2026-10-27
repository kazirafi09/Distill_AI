"""Recruiter-owned question bank documents and indexing status."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
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


class QuestionBank(Base):
    __tablename__ = "question_banks"

    __table_args__ = (
        UniqueConstraint(
            "recruiter_id",
            "file_sha256",
            name="uq_question_banks_recruiter_file",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_question_banks_status",
        ),
        CheckConstraint(
            "file_size_bytes > 0",
            name="ck_question_banks_positive_size",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4,
    )
    recruiter_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_type: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    extracted_text: Mapped[str | None] = mapped_column(Text)
    sources: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSONB, default=list)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending",
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    chunk_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
    )
    index_version: Mapped[str | None] = mapped_column(String(100))
    is_archived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )
