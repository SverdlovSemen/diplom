from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


GaugeType = Literal["analog", "digital", "digital_segment"]
CaptureMode = Literal["continuous", "schedule"]


class LoggerBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=255)
    stream_key: str = Field(min_length=1, max_length=128)
    gauge_type: GaugeType
    unit: str = Field(default="unit", max_length=32)
    min_value: float | None = None
    max_value: float | None = None
    sample_interval_sec: int = Field(default=5, ge=1, le=3600)
    enabled: bool = True
    capture_mode: CaptureMode = "continuous"
    schedule_start_hour_utc: int | None = Field(default=None, ge=0, le=23)
    schedule_end_hour_utc: int | None = Field(default=None, ge=0, le=23)
    image_retention_days: int | None = Field(default=None, ge=1, le=3650)
    roi_json: str | None = None
    calibration_json: str | None = None


class LoggerCreate(LoggerBase):
    pass


class LoggerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=255)
    gauge_type: GaugeType | None = None
    unit: str | None = Field(default=None, max_length=32)
    min_value: float | None = None
    max_value: float | None = None
    sample_interval_sec: int | None = Field(default=None, ge=1, le=3600)
    enabled: bool | None = None
    capture_mode: CaptureMode | None = None
    schedule_start_hour_utc: int | None = Field(default=None, ge=0, le=23)
    schedule_end_hour_utc: int | None = Field(default=None, ge=0, le=23)
    image_retention_days: int | None = Field(default=None, ge=1, le=3650)
    roi_json: str | None = None
    calibration_json: str | None = None


class LoggerOut(LoggerBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    last_stream_seen_at: datetime | None = None
    last_stream_gap_at: datetime | None = None
    last_ingest_error: str | None = None

    model_config = {"from_attributes": True}


class LoggerStatus(BaseModel):
    stream_active: bool
    ingest_last_attempt_at: datetime | None = None
    ingest_last_success_at: datetime | None = None
    ingest_last_error: str | None = None
    last_measurement_at: datetime | None = None
    last_ok: bool | None = None
    last_error: str | None = None
    stream_unavailable_persisted: bool = Field(
        default=False,
        description="По БД: последняя зафиксированная проблема с потоком новее последнего успешного кадра.",
    )


class LoggerWithStatus(LoggerOut):
    status: LoggerStatus


class LoggerBulkMonitoringUpdate(BaseModel):
    """Массовое обновление мониторинговых параметров для всех логеров."""

    sample_interval_sec: int | None = Field(default=None, ge=1, le=3600)
    enabled: bool | None = None
    capture_mode: CaptureMode | None = None
    schedule_start_hour_utc: int | None = Field(default=None, ge=0, le=23)
    schedule_end_hour_utc: int | None = Field(default=None, ge=0, le=23)
    image_retention_days: int | None = Field(default=None, ge=1, le=3650)
    apply_to_disabled: bool = True


class BulkUpdateResult(BaseModel):
    updated: int

