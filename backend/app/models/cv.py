from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CVSource(Base):
    __tablename__ = "cv_sources"
    __table_args__ = (
        UniqueConstraint("spreadsheet_id", "sheet_tab_id", name="uq_cv_sources_sheet_tab"),
        CheckConstraint(
            "(spreadsheet_id IS NULL AND sheet_tab_id IS NULL) OR "
            "(spreadsheet_id IS NOT NULL AND sheet_tab_id IS NOT NULL AND sheet_tab_id >= 0)",
            name="ck_cv_sources_sheet_mapping",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    job_description_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    drive_folder_id: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True
    )
    spreadsheet_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    sheet_tab_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class CVSourceCheck(Base):
    """Durable metadata-only checks; importing remains a recruiter action."""
    __tablename__ = "cv_source_checks"
    source_id: Mapped[UUID] = mapped_column(ForeignKey("cv_sources.id", ondelete="CASCADE"), primary_key=True)
    pending_file_ids: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class CVDocument(Base):
    __tablename__ = "cv_documents"
    __table_args__ = (
        UniqueConstraint(
            "job_description_id",
            "drive_file_id",
            name="uq_cv_documents_job_drive_file",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    job_description_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    drive_file_id: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    application_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("candidate_applications.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class CVFileVersion(Base):
    __tablename__ = "cv_file_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'downloading', 'downloaded', 'failed')",
            name="ck_cv_file_versions_status",
        ),
        CheckConstraint(
            "file_size_bytes IS NULL OR "
            "(file_size_bytes > 0 AND file_size_bytes <= 10485760)",
            name="ck_cv_file_versions_size",
        ),
        CheckConstraint(
            "status != 'downloaded' OR "
            "(relative_path IS NOT NULL AND file_sha256 IS NOT NULL "
            "AND file_size_bytes IS NOT NULL)",
            name="ck_cv_file_versions_downloaded_metadata",
        ),
    )

    # Also used as the UUID-based storage filename.
    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid4
    )
    cv_document_id: Mapped[UUID] = mapped_column(
        ForeignKey("cv_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    original_filename: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    mime_type: Mapped[str] = mapped_column(
        String(255), nullable=False
    )
    relative_path: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True
    )
    file_size_bytes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    file_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    source_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default="pending"
    )
    error_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class Candidate(Base):
    __tablename__ = "candidates"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    recruiter_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CandidateApplication(Base):
    __tablename__ = "candidate_applications"
    __table_args__ = (
        UniqueConstraint("candidate_id", "job_description_id", name="uq_candidate_applications_candidate_job"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    candidate_id: Mapped[UUID] = mapped_column(
        ForeignKey("candidates.id", ondelete="CASCADE"), nullable=False
    )
    job_description_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_id: Mapped[UUID] = mapped_column(
        ForeignKey("cv_sources.id", ondelete="CASCADE"), nullable=False
    )
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    # Keep the sheet's formatted timestamp without guessing its locale/timezone.
    submitted_at_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
