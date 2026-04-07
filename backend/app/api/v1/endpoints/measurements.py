from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db_session
from app.schemas.measurement import MeasurementOut
from app.services.measurements import list_measurements

router = APIRouter()


@router.get("/", response_model=list[MeasurementOut])
async def api_list_measurements(
    logger_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    session: AsyncSession = Depends(get_db_session),
) -> list[MeasurementOut]:
    return await list_measurements(session, logger_id=logger_id, limit=limit)

