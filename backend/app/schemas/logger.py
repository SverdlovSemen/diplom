from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


GaugeType = Literal["analog", "digital"]


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
    roi_json: str | None = None
    calibration_json: str | None = None


class LoggerOut(LoggerBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class LoggerStatus(BaseModel):
    stream_active: bool
    ingest_last_attempt_at: datetime | None = None
    ingest_last_success_at: datetime | None = None
    ingest_last_error: str | None = None
    last_measurement_at: datetime | None = None
    last_ok: bool | None = None
    last_error: str | None = None


class LoggerWithStatus(LoggerOut):
    status: LoggerStatus

