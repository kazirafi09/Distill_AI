from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

class JDConversation(Base):
    __tablename__ = "jd_conversations"

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    recruiter_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )


class JobDescription(Base):
    __tablename__ = "job_descriptions"

    __table_args__ = (
        UniqueConstraint(
            "number",
            name="uq_job_descriptions_number",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("jd_conversations.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    requirements: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    number: Mapped[int] = mapped_column(
        BigInteger,
        Identity(start=1),
        nullable=False,
    )

    # Null on legacy JDs; no fabricated/backfilled screening profiles.
    screening_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    profile_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    google_form_url: Mapped[str | None] = mapped_column(Text, nullable=True)


class DiscordJobPost(Base):
    __tablename__ = "discord_job_posts"

    __table_args__ = (
        UniqueConstraint(
            "job_description_id",
            name="uq_discord_job_posts_job_description_id",
        ),
        CheckConstraint(
            "status IN ('sending', 'posted', 'failed', 'unknown')",
            name="ck_discord_job_posts_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )

    job_description_id: Mapped[UUID] = mapped_column(
        ForeignKey("job_descriptions.id", ondelete="CASCADE"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    channel_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    message_id: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
    )

    message_url: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    error_code: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
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
