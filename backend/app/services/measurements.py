from __future__ import annotations

import uuid

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.measurement import Measurement


async def list_measurements(
    session: AsyncSession,
    *,
    logger_id: uuid.UUID | None = None,
    limit: int = 100,
) -> list[Measurement]:
    query = select(Measurement).order_by(desc(Measurement.captured_at)).limit(limit)
    if logger_id is not None:
        query = query.where(Measurement.logger_id == logger_id)
    result = await session.execute(query)
    return list(result.scalars().all())


async def get_last_measurement_for_logger(
    session: AsyncSession,
    logger_id: uuid.UUID,
) -> Measurement | None:
    result = await session.execute(
        select(Measurement).where(Measurement.logger_id == logger_id).order_by(desc(Measurement.captured_at)).limit(1)
    )
    return result.scalar_one_or_none()

