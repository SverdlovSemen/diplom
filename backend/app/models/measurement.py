from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    logger_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("loggers.id", ondelete="CASCADE"))

    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="unit")

    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Допустимый диапазон показаний задаётся в loggers.min_value/max_value (не путать с min/max шкалы в calibration_json).
    out_of_range: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    cv_warnings_json: Mapped[str | None] = mapped_column(String, nullable=True)

    image_path: Mapped[str | None] = mapped_column(String(512), nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

