from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel


class MeasurementOut(BaseModel):
    id: uuid.UUID
    logger_id: uuid.UUID
    value: float | None
    unit: str
    ok: bool
    error: str | None
    out_of_range: bool | None = None
    cv_warnings_json: str | None = None
    image_path: str | None
    captured_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}


class MeasurementListOut(BaseModel):
    items: list[MeasurementOut]
    total: int


class MeasurementStatsOut(BaseModel):
    """Сводка по измерениям за интервал (те же фильтры, что у списка)."""

    period_from: datetime | None = None
    period_to: datetime | None = None
    logger_id: uuid.UUID | None = None

    count: int
    value_count: int
    value_min: float | None = None
    value_max: float | None = None
    value_avg: float | None = None

    recognition_fail_count: int
    out_of_range_count: int
    cv_warnings_count: int


class MeasurementAlertOut(BaseModel):
    measurement_id: uuid.UUID
    logger_id: uuid.UUID
    logger_name: str
    captured_at: datetime
    value: float | None
    unit: str
    error: str | None
    image_path: str | None

