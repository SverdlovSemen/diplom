from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class GaugeType(str, enum.Enum):
    analog = "analog"
    digital = "digital"


class CaptureMode(str, enum.Enum):
    continuous = "continuous"
    schedule = "schedule"


class Logger(Base):
    __tablename__ = "loggers"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)

    # Человекочитаемое имя/описание и метки размещения
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    location: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Видеопоток: ключ (stream key) в RTMP `live/<stream_key>`
    stream_key: Mapped[str] = mapped_column(String(128), unique=True, index=True, nullable=False)

    gauge_type: Mapped[GaugeType] = mapped_column(Enum(GaugeType, name="gauge_type"), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False, default="unit")

    min_value: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_value: Mapped[float | None] = mapped_column(Float, nullable=True)

    sample_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capture_mode: Mapped[CaptureMode] = mapped_column(
        Enum(CaptureMode, name="capture_mode"),
        nullable=False,
        default=CaptureMode.continuous,
    )
    # Для режима schedule: окно активности в UTC-часах [start, end), поддерживает переход через полночь.
    schedule_start_hour_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    schedule_end_hour_utc: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Политика хранения JPEG: удалять файлы старше N дней (сохраняем запись measurement, image_path очищаем).
    image_retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # ROI и калибровка (на этапе скелета — JSON-строки; позже можно вынести в отдельные таблицы/JSONB)
    roi_json: Mapped[str | None] = mapped_column(String, nullable=True)
    calibration_json: Mapped[str | None] = mapped_column(String, nullable=True)

    # Персистентное состояние видеопотока (переживает перезапуск backend; дополняет in-memory ingest)
    last_stream_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_stream_gap_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_ingest_error: Mapped[str | None] = mapped_column(String(512), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

