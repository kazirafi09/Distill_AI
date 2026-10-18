from datetime import datetime

from sqlalchemy import DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ModelRateLimit(Base):
    __tablename__ = "model_rate_limits"

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    next_request_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
