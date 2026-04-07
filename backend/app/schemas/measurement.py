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
    image_path: str | None
    captured_at: datetime
    created_at: datetime

    model_config = {"from_attributes": True}

